from langgraph.graph import StateGraph, END
from ..models.state import AgentState
from .subgraph1 import extract_company_info_node
from .subgraph2 import analyze_sector_node
from .subgraph3 import analyze_financial_node
from .assembler import assemble_report_node, quality_review_node
from ..utils.logger import get_logger, setup_langsmith_tracing
from ..config import get_output_dir as _default_output_dir

logger = get_logger("graph")


# ─────────────────────────────────────────────────────────────────────────────
# Routing logic
# ─────────────────────────────────────────────────────────────────────────────

def route_after_review(state: AgentState) -> str:
    """Conditional edge: decide whether to retry a weak section or finish.

    Self-correction loop (max 1 retry):
    - If overall score ≥ 7 → done, go to END.
    - If retry_count ≥ 1  → already retried once, accept result and END.
    - Otherwise           → route back to the section with the lowest quality
                            score so it can use quality_feedback to self-correct.

    This makes the graph truly agentic: it observes output quality and takes
    a corrective action (re-run the weakest analysis node) rather than blindly
    outputting whatever was generated on the first pass.
    """
    result = state.get("quality_review_result") or {}
    score = result.get("score", 10)
    retry_count = state.get("retry_count", 0)

    if score >= 7 or retry_count >= 2:
        # retry_count is incremented by quality_review_node before we read it,
        # so ≥ 2 means this is the second review (after one retry).
        logger.info(f"Routing → END  (score={score} retry_count={retry_count})")
        return END

    financial_q = result.get("financial_quality", 10)
    sector_q = result.get("sector_quality", 10)

    if financial_q <= sector_q and financial_q < 7:
        logger.info(
            f"Routing → analyze_financial for retry  "
            f"(financial_quality={financial_q} < sector_quality={sector_q})"
        )
        return "analyze_financial"
    elif sector_q < 7:
        logger.info(
            f"Routing → analyze_sector for retry  (sector_quality={sector_q})"
        )
        return "analyze_sector"

    logger.info(f"Routing → END  (no section below threshold, score={score})")
    return END


# ─────────────────────────────────────────────────────────────────────────────
# Graph builder
# ─────────────────────────────────────────────────────────────────────────────

def build_credit_proposal_graph():
    """Build and compile the LangGraph credit proposal graph.

    Graph topology
    ──────────────
                 extract_company_info
                   /              \\
         analyze_sector    analyze_financial   ← parallel fan-out
                   \\              /
                   assemble_report             ← fan-in (join)
                         |
                   quality_review
                    /          \\
                 END       analyze_financial   ← conditional retry
                        or analyze_sector

    Key design choices
    ──────────────────
    1. Parallel fan-out: sector and financial analysis are independent and run
       concurrently, halving wall-clock time on I/O-bound LLM calls.
       State fields written by the two nodes are disjoint (section_2_sector vs
       section_3_financial), so no conflict.  The `errors` and `messages` lists
       use Annotated[list, add] reducers in AgentState to safely merge parallel
       writes.

    2. Self-correction loop: quality_review_node scores each output section.
       route_after_review() uses those scores to conditionally re-run the
       weakest section (max 1 retry) — the re-run node reads quality_feedback
       from state and injects it into its LLM prompt, making the retry smarter
       than a blind re-run.

    3. State immutability on retry: the retry path routes back to either
       analyze_sector or analyze_financial directly (not through the parallel
       fan-out), so only the chosen node re-runs.  assemble_report then
       re-assembles with the updated section, and quality_review runs again.
    """
    builder = StateGraph(AgentState)

    # Add nodes
    builder.add_node("extract_company_info", extract_company_info_node)
    builder.add_node("analyze_sector",       analyze_sector_node)
    builder.add_node("analyze_financial",    analyze_financial_node)
    builder.add_node("assemble_report",      assemble_report_node)
    builder.add_node("quality_review",       quality_review_node)

    # Entry point
    builder.set_entry_point("extract_company_info")

    # Fan-out: sector + financial run in parallel after company info is ready
    builder.add_edge("extract_company_info", "analyze_sector")
    builder.add_edge("extract_company_info", "analyze_financial")

    # Fan-in: assemble only after BOTH analyses complete (LangGraph join semantics)
    builder.add_edge("analyze_sector",    "assemble_report")
    builder.add_edge("analyze_financial", "assemble_report")

    # Linear: assemble → review
    builder.add_edge("assemble_report", "quality_review")

    # Self-correction: conditional routing from quality_review
    builder.add_conditional_edges(
        "quality_review",
        route_after_review,
        {
            "analyze_financial": "analyze_financial",
            "analyze_sector":    "analyze_sector",
            END:                 END,
        },
    )

    return builder.compile()


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

def run_credit_proposal(
    company_name: str,
    md_company_info_path: str,
    pdf_dir_path: str,
    output_dir: str | None = None,
    company: str = "unknown",
) -> dict:
    """Run the credit proposal agent end-to-end.

    Args:
        company_name:          Display name of the company.
        md_company_info_path:  Path to markdown company info file.
        pdf_dir_path:          Directory containing 2022/2023/2024 PDF subdirs.
        output_dir:            Output directory for results.
        company:               Short company code, e.g. "mst" (used for OCR cache).

    Returns:
        Final state dict with all results.
    """
    setup_langsmith_tracing()

    graph = build_credit_proposal_graph()

    resolved_output_dir = output_dir or str(_default_output_dir(company))

    initial_state: AgentState = {
        "company":                  company,
        "company_name":             company_name,
        "md_company_info_path":     md_company_info_path,
        "pdf_dir_path":             pdf_dir_path,
        "output_dir":               resolved_output_dir,
        # Intermediate
        "company_info":             None,
        "sector_info":              None,
        "financial_data":           None,
        # Sections
        "section_1_company":        None,
        "section_2_sector":         None,
        "section_3_financial":      None,
        # Final output
        "final_report_md":          None,
        "final_report_docx_path":   None,
        "final_report_memo_docx_path": None,
        # Quality feedback loop
        "retry_count":              0,
        "quality_review_result":    None,
        "quality_feedback":         None,
        # Control (Annotated list fields start empty; reducers append to them)
        "errors":                   [],
        "current_step":             "started",
        "messages":                 [],
    }

    logger.info(f"{'='*55}")
    logger.info(f"Starting credit proposal — company='{company}'  name='{company_name}'")
    logger.info(f"  md_path  : {md_company_info_path}")
    logger.info(f"  pdf_dir  : {pdf_dir_path}")
    logger.info(f"  output   : {output_dir}")
    logger.info(f"{'='*55}")

    import time
    t0 = time.perf_counter()
    final_state = graph.invoke(initial_state)
    elapsed = time.perf_counter() - t0

    review = final_state.get("quality_review_result") or {}
    logger.info(f"{'='*55}")
    logger.info(f"PIPELINE COMPLETE  [{elapsed:.1f}s]")
    logger.info(f"  step       : {final_state.get('current_step')}")
    logger.info(f"  retries    : {final_state.get('retry_count', 0)}")
    logger.info(f"  quality    : {review.get('score', '?')}/10  "
                f"(completeness={review.get('completeness','?')} "
                f"sector={review.get('sector_quality','?')} "
                f"financial={review.get('financial_quality','?')})")
    logger.info(f"  md saved   : {final_state.get('final_report_md') is not None}")
    logger.info(f"  form docx  : {final_state.get('final_report_docx_path')}")
    logger.info(f"  memo docx  : {final_state.get('final_report_memo_docx_path')}")
    if final_state.get('errors'):
        for err in final_state['errors']:
            logger.error(f"  ERROR      : {err}")
    logger.info(f"{'='*55}")

    return final_state
