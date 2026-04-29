import uuid
import time as _time
from langgraph.graph import StateGraph, END
from ..models.state import AgentState
from .subgraph1 import extract_company_info_node
from .subgraph2 import analyze_sector_node
from .subgraph3 import analyze_financial_node
from .assembler import assemble_report_node, quality_review_node
from ..utils.logger import get_logger, setup_langsmith_tracing
from ..utils.audit import get_audit_logger
from ..utils.checkpoint import save_run_meta, save_node_checkpoint
from ..config import get_output_dir as _default_output_dir

logger = get_logger("graph")


# ─────────────────────────────────────────────────────────────────────────────
# Routing logic
# ─────────────────────────────────────────────────────────────────────────────

def human_escalation_node(state: AgentState) -> dict:
    """Escalation node: format a structured report for human review.

    Triggered when:
    - After 1 retry, quality score is still < 7, OR
    - More than 3 low-confidence claims detected in verification_summary.

    The escalation report summarises what the AI couldn't resolve so a credit
    analyst can complete the review manually.
    """
    result = state.get("quality_review_result") or {}
    score = result.get("score", "N/A")
    issues = result.get("issues", [])
    ver_summary = state.get("verification_summary") or {}
    claims = state.get("claim_verifications") or []
    low_conf_claims = [c for c in claims if isinstance(c, dict) and c.get("confidence", 1.0) < 0.5]

    lines = [
        "# BÁO CÁO CHUYỂN TIẾP CÁN BỘ THẨM ĐỊNH",
        "",
        f"**Điểm chất lượng AI:** {score}/10",
        f"**Số lần retry:** {state.get('retry_count', 0)}",
        f"**Claims cần xem lại:** {len(low_conf_claims)}",
        "",
        "## Vấn đề AI không giải quyết được",
    ]
    for issue in issues:
        lines.append(f"- {issue}")

    if low_conf_claims:
        lines.append("")
        lines.append("## Claims có độ tin cậy thấp (< 0.5)")
        for c in low_conf_claims:
            lines.append(f"- [{c.get('claim_type', '?')}] {c.get('claim_text', '?')} (confidence={c.get('confidence', '?')})")
            for iss in c.get("issues", []):
                lines.append(f"  - {iss}")

    ver_errors = ver_summary.get("error_details", [])
    if ver_errors:
        lines.append("")
        lines.append("## Lỗi xác minh đa tầng")
        for e in ver_errors:
            lines.append(f"- {e}")

    lines += [
        "",
        "## Hành động cần thiết",
        "- Kiểm tra số liệu tài chính thủ công với file BCTC gốc",
        "- Xác nhận thông tin công ty với Giấy CNĐKKD",
        "- Bổ sung các thông tin còn thiếu trước khi trình duyệt",
    ]

    escalation_md = "\n".join(lines)
    logger.warning(
        f"Escalating to human review — score={score}  "
        f"low_conf_claims={len(low_conf_claims)}  issues={len(issues)}"
    )

    # Checkpoint escalation
    run_id = state.get("run_id", "unknown")
    save_node_checkpoint(run_id, "escalation", {
        "score": score, "issues": issues, "low_conf_count": len(low_conf_claims)
    })
    audit = get_audit_logger(run_id)
    audit.tool_call("human_escalation", score=score, low_conf_claims=len(low_conf_claims))

    return {
        "escalation_report": escalation_md,
        "current_step": "escalated_to_human",
    }


def route_after_review(state: AgentState) -> str:
    """Conditional edge: decide whether to retry, escalate, or finish.

    Self-correction loop (max 1 retry) with escalation:
    - If overall score ≥ 7 → done, go to END.
    - If retry_count == 0 and score < 7 → retry weakest section.
    - If retry_count ≥ 1 and score < 7 → escalate to human.
    - If low_confidence claims > 3 → escalate to human.

    This makes the graph truly agentic: it observes output quality and takes
    a corrective action (re-run the weakest analysis node) rather than blindly
    outputting whatever was generated on the first pass.
    """
    result = state.get("quality_review_result") or {}
    score = result.get("score", 10)
    retry_count = state.get("retry_count", 0)

    # Check for low-confidence claims requiring escalation
    ver_summary = state.get("verification_summary") or {}
    low_conf_count = ver_summary.get("low_confidence_count", 0)
    if low_conf_count > 3:
        logger.warning(
            f"Routing → human_escalation  (low_confidence_claims={low_conf_count} > 3)"
        )
        return "human_escalation"

    if score >= 7:
        logger.info(f"Routing → END  (score={score}≥7)")
        return END

    if retry_count >= 2:
        # After 1 retry (retry_count incremented to 2 by quality_review), escalate
        logger.warning(
            f"Routing → human_escalation  (score={score}<7 after {retry_count} retries)"
        )
        return "human_escalation"

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
                   assemble_report             ← fan-in + multi-layer verifier
                         |
                   quality_review              ← LLM-as-Judge + claim verifications
                  /      |      \\
               END    retry     human_escalation  ← escalate if score<7 after retry

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
    builder.add_node("human_escalation",     human_escalation_node)

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

    # Self-correction + escalation: conditional routing from quality_review
    builder.add_conditional_edges(
        "quality_review",
        route_after_review,
        {
            "analyze_financial": "analyze_financial",
            "analyze_sector":    "analyze_sector",
            "human_escalation":  "human_escalation",
            END:                 END,
        },
    )

    # human_escalation ends the pipeline
    builder.add_edge("human_escalation", END)

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

    run_id = str(uuid.uuid4())
    audit = get_audit_logger(run_id)
    save_run_meta(run_id, company)
    audit.pipeline_start(company, company_name)

    initial_state: AgentState = {
        # ── Run identity ──────────────────────────────────────────────────
        "run_id":                   run_id,
        # ── Input ─────────────────────────────────────────────────────────
        "company":                  company,
        "company_name":             company_name,
        "md_company_info_path":     md_company_info_path,
        "pdf_dir_path":             pdf_dir_path,
        "output_dir":               resolved_output_dir,
        # ── Intermediate ──────────────────────────────────────────────────
        "company_info":             None,
        "sector_info":              None,
        "financial_data":           None,
        # ── Sections ──────────────────────────────────────────────────────
        "section_1_company":        None,
        "section_2_sector":         None,
        "section_3_financial":      None,
        # ── Final output ──────────────────────────────────────────────────
        "final_report_md":          None,
        "final_report_docx_path":   None,
        "final_report_memo_docx_path": None,
        # ── Quality feedback loop ─────────────────────────────────────────
        "retry_count":              0,
        "quality_review_result":    None,
        "quality_feedback":         None,
        # ── Verification & escalation ─────────────────────────────────────
        "claim_verifications":      None,
        "verification_summary":     None,
        "escalation_report":        None,
        # ── Control ───────────────────────────────────────────────────────
        "errors":                   [],
        "current_step":             "started",
        "messages":                 [],
    }

    logger.info(f"{'='*55}")
    logger.info(f"Starting credit proposal — run_id={run_id}  company='{company}'  name='{company_name}'")
    logger.info(f"  md_path  : {md_company_info_path}")
    logger.info(f"  pdf_dir  : {pdf_dir_path}")
    logger.info(f"  output   : {output_dir}")
    logger.info(f"{'='*55}")

    t0 = _time.perf_counter()
    final_state = graph.invoke(initial_state)
    elapsed = _time.perf_counter() - t0

    review = final_state.get("quality_review_result") or {}
    errors = final_state.get("errors") or []
    audit.pipeline_end(
        elapsed_s=elapsed,
        quality_score=review.get("score"),
        retry_count=final_state.get("retry_count", 0),
        errors=errors,
    )

    logger.info(f"{'='*55}")
    logger.info(f"PIPELINE COMPLETE  [{elapsed:.1f}s]  run_id={run_id}")
    logger.info(f"  step       : {final_state.get('current_step')}")
    logger.info(f"  retries    : {final_state.get('retry_count', 0)}")
    logger.info(f"  quality    : {review.get('score', '?')}/10  "
                f"(completeness={review.get('completeness','?')} "
                f"sector={review.get('sector_quality','?')} "
                f"financial={review.get('financial_quality','?')})")
    logger.info(f"  md saved   : {final_state.get('final_report_md') is not None}")
    logger.info(f"  form docx  : {final_state.get('final_report_docx_path')}")
    logger.info(f"  memo docx  : {final_state.get('final_report_memo_docx_path')}")
    if errors:
        for err in errors:
            logger.error(f"  ERROR      : {err}")
    logger.info(f"{'='*55}")

    return final_state
