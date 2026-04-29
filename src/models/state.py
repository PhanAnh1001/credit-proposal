from typing import TypedDict, Optional, Any, Annotated
from operator import add
from .company import CompanyInfo
from .financial import FinancialData


class AgentState(TypedDict):
    # ── Run identity ─────────────────────────────────────────────────────────
    run_id: str        # UUID4 assigned at pipeline start; groups all audit events

    # ── Input ────────────────────────────────────────────────────────────────
    company: str       # company code / folder name, e.g. "mst"
    company_name: str
    md_company_info_path: str
    pdf_dir_path: str  # e.g. data/uploads/mst/financial-statements/pdf/
    output_dir: str

    # ── Intermediate results ─────────────────────────────────────────────────
    company_info: Optional[CompanyInfo]
    sector_info: Optional[dict]
    financial_data: Optional[FinancialData]

    # ── Drafted sections (markdown text) ────────────────────────────────────
    section_1_company: Optional[str]
    section_2_sector: Optional[str]
    section_3_financial: Optional[str]

    # ── Final output ─────────────────────────────────────────────────────────
    final_report_md: Optional[str]
    final_report_docx_path: Optional[str]       # VPBank form template (Output 1)
    final_report_memo_docx_path: Optional[str]  # analyst memo DOCX (Output 2+3)

    # ── Quality feedback loop ────────────────────────────────────────────────
    # quality_review_node populates these; route_after_review reads them.
    retry_count: int                     # Number of retries so far (max 1)
    quality_review_result: Optional[dict]  # {score, completeness, sector_quality,
                                           #  financial_quality, issues, summary}
    quality_feedback: Optional[str]      # Concrete improvement hints for retry nodes

    # ── Verification & escalation ────────────────────────────────────────────
    claim_verifications: Optional[list]  # list[ClaimVerification] as dicts
    verification_summary: Optional[dict] # {layers_run, total_claims, low_confidence_count}
    escalation_report: Optional[str]     # Markdown escalation report for human review

    # ── Control flow ─────────────────────────────────────────────────────────
    # Annotated with `add` reducer so parallel branches safely append errors/messages
    # without last-writer-wins overwriting.  Nodes should return ONLY new items,
    # e.g. {"errors": ["new error"]}  — NOT state['errors'] + ["new error"].
    errors: Annotated[list[str], add]
    current_step: Annotated[str, lambda a, b: b]  # last-write wins; safe for parallel nodes
    messages: Annotated[list[Any], add]
