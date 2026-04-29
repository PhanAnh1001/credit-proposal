import json
import os
from pathlib import Path
from datetime import datetime
from ..models.state import AgentState
from ..models.verification import ClaimVerification, VerificationSummary
from ..utils.llm import get_judge_llm, strip_llm_json, invoke_with_retry
from ..utils.docx_template import render_analyst_memo, render_from_template
from ..utils.logger import get_logger, timed_node
from ..utils.audit import get_audit_logger
from ..tools.multi_layer_verifier import run_all_layers
from ..config import get_output_dir as _default_output_dir
from langchain_core.messages import HumanMessage, SystemMessage

logger = get_logger("assembler")


@timed_node("assemble_report")
def assemble_report_node(state: AgentState) -> dict:
    """Node: Assemble all sections into final credit proposal."""
    company_info = state.get('company_info')
    # Prefer the CLI-provided company_name for the header; fall back to extracted name
    company_name = (state.get('company_name')
                    or (company_info.company_name if company_info else None)
                    or 'Unknown')
    logger.info(f"Assembling report for company: {company_name}")

    section1 = state.get('section_1_company') or '# Thông tin Khách hàng\n*(Không có dữ liệu)*'
    section2 = state.get('section_2_sector') or '# Phụ lục A: Thông tin lĩnh vực kinh doanh\n*(Không có dữ liệu)*'
    section3 = state.get('section_3_financial') or '# Phụ lục B: Phân tích tình hình tài chính\n*(Không có dữ liệu)*'

    # Log which sections have real content
    for label, sec in [("Section 1", section1), ("Section 2", section2), ("Section 3", section3)]:
        status = f"{len(sec)} chars" if "Không có dữ liệu" not in sec else "EMPTY (placeholder)"
        logger.debug(f"{label}: {status}")

    # Generate cover/header
    today = datetime.now().strftime("%d/%m/%Y")
    header = _build_report_header(company_name, today)

    # Assemble: header + 3 required outputs
    full_report = (
        f"{header}\n\n"
        f"{section1}\n\n"
        f"{section2}\n\n"
        f"{section3}\n"
    )
    logger.info(f"Full report assembled — {len(full_report)} chars")

    # Save markdown
    company = state.get('company', 'unknown')
    output_dir = state.get('output_dir') or str(_default_output_dir(company))
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Markdown = full analyst memo (all 3 outputs) — same content as credit-analyst-memo.docx
    md_path = os.path.join(output_dir, 'credit-analyst-memo.md')
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(full_report)
    logger.info(f"Markdown saved → {md_path}")

    # Output 1 DOCX: fill VPBank form template with company info + financial numbers
    docx_path = os.path.join(output_dir, 'credit-proposal.docx')
    try:
        render_from_template(
            output_path=docx_path,
            company_info=company_info,
            financial_data=state.get('financial_data'),
        )
        logger.info(f"Form DOCX saved → {docx_path}")
    except Exception as e:
        logger.error(f"Form DOCX failed: {e}", exc_info=True)
        docx_path = None

    # Output 2+3 DOCX: analyst memo with sector + financial analysis
    memo_docx_path = os.path.join(output_dir, 'credit-analyst-memo.docx')
    try:
        render_analyst_memo(
            output_path=memo_docx_path,
            company_name=company_name,
            section2=state.get('section_2_sector'),
            section3=state.get('section_3_financial'),
        )
        logger.info(f"Analyst memo DOCX saved → {memo_docx_path}")
    except Exception as e:
        logger.error(f"Analyst memo DOCX failed: {e}", exc_info=True)
        memo_docx_path = None

    # Multi-layer verification: run all 4 layers on assembled report + data
    verifier_result = run_all_layers(dict(state))
    ver_summary = verifier_result.get("verification_summary", {})
    logger.info(
        f"Multi-layer verifier: {ver_summary.get('passed', '?')}/{ver_summary.get('total_checks', '?')} pass  "
        f"errors={ver_summary.get('errors', 0)}  warns={ver_summary.get('warnings', 0)}"
    )

    return {
        "final_report_md": full_report,
        "final_report_docx_path": docx_path,
        "final_report_memo_docx_path": memo_docx_path,
        "current_step": "completed",
        "verification_summary": ver_summary,
        "errors": verifier_result.get("errors", []),
    }


@timed_node("quality_review")
def quality_review_node(state: AgentState) -> dict:
    """Optional node: LLM-as-Judge quality review of the final report."""
    final_report = state.get('final_report_md', '')
    if not final_report:
        logger.warning("No final report to review — skipping")
        return {"current_step": "review_skipped"}

    logger.info("Running LLM-as-Judge quality review")
    llm = get_judge_llm()

    messages = [
        SystemMessage(content="""Bạn là chuyên viên thẩm định tín dụng ngân hàng Việt Nam.
Đánh giá chất lượng tờ trình phân tích tín dụng nội bộ gồm 3 phần do AI tạo ra.
Tài liệu này KHÔNG phải giấy đề nghị vay vốn — đây là tờ trình phân tích để ngân hàng ra quyết định.

Tiêu chí đánh giá theo 3 output của AI:

Output 1 — Thông tin khách hàng (completeness):
- Có đủ tên, MST/CNĐKDN, địa chỉ, ngành nghề, vốn điều lệ không?
- Có danh sách cổ đông lớn với tỷ lệ sở hữu không?
- Có người đại diện pháp luật không?

Output 2 — Phân tích lĩnh vực kinh doanh (sector_quality):
- Có mô tả tổng quan ngành và xu hướng phát triển không?
- Có nhận diện rủi ro ngành cụ thể không?
- Thông tin có phù hợp với ngành kinh doanh của công ty không?

Output 3 — Phân tích tài chính (financial_quality):
- Có số liệu bảng CĐKT và KQKD từ BCTC không?
- Có tính và trình bày chỉ số tài chính (ROE, ROA, D/E, current ratio...) không?
- Nhận xét phân tích có logic, bám sát số liệu, so sánh các năm không?
- Số liệu có nhất quán nội bộ (tổng tài sản = nợ + VCSH...) không?

LƯU Ý: Không trừ điểm vì thiếu số tiền vay, TSBĐ, hay thông tin khách hàng điền tay —
những mục đó nằm ngoài scope của AI agent này.

Trả về JSON thuần (không markdown):
{
  "score": <0-10>,
  "completeness": <0-10>,
  "sector_quality": <0-10>,
  "financial_quality": <0-10>,
  "issues": ["mô tả vấn đề cụ thể 1", "..."],
  "summary": "nhận xét tổng thể ngắn gọn"
}
"""),
        HumanMessage(content=(
            # Sampling windows are sized to guarantee the judge sees the key content:
            # S1 [:2000]: captures through shareholders table (~1700 chars in)
            # S2 [:3000]: captures through risk analysis section (~2000 chars in)
            # S3 [:3000]: captures full ratio table + narrative (~1700-2800 chars in)
            # Total ~8000 chars ≈ 5300 tokens; stays within gpt-oss-20b 8K context
            "Tờ trình phân tích tín dụng (trích đại diện từng phần):\n\n"
            + "\n\n".join([
                (state.get("section_1_company") or "")[:2000],
                (state.get("section_2_sector")  or "")[:3000],
                (state.get("section_3_financial") or "")[:3000],
            ])
        ))
    ]

    try:
        response = invoke_with_retry(llm, messages, retries=2, sleep_s=12)
        raw = strip_llm_json(response.content)

        if not raw or not raw.startswith("{"):
            logger.warning(f"Quality review: non-JSON response — {raw[:80]!r}")
            return {"current_step": "review_done"}

        review = json.loads(raw)
        score = review.get('score', 10)
        completeness = review.get('completeness', 10)
        sector_q = review.get('sector_quality', 10)
        financial_q = review.get('financial_quality', 10)
        summary = review.get('summary', '')
        issues = review.get('issues', [])

        logger.info(
            f"Quality score: {score}/10  "
            f"(completeness={completeness} sector={sector_q} financial={financial_q})  "
            f"—  {summary}"
        )
        if issues:
            for issue in issues:
                logger.warning(f"  Issue: {issue}")

        # Build actionable feedback for retry nodes.
        # Only pass issues relevant to the section being retried — sending
        # "fix shareholders" to analyze_financial is useless and confuses the LLM.
        feedback: str | None = None
        retry_count = state.get("retry_count", 0)
        if score < 7 and retry_count == 0 and issues:
            if financial_q <= sector_q:
                weak_section = "phân tích tài chính"
                section_kw = ["tài chính", "financial", "ratio", "chỉ số", "bảng",
                              "số liệu", "lợi nhuận", "doanh thu", "bctc", "thanh toán"]
            else:
                weak_section = "phân tích ngành"
                section_kw = ["ngành", "sector", "rủi ro", "risk", "xu hướng",
                              "cạnh tranh", "thị trường", "market"]
            relevant = [i for i in issues if any(k in i.lower() for k in section_kw)]
            top_issues = "; ".join((relevant or issues)[:3])
            feedback = f"Cải thiện {weak_section}. Vấn đề cần sửa: {top_issues}"
            logger.info(f"Quality feedback generated for retry: {feedback!r}")

        # Claim-level verification: derive per-claim confidence from section scores
        run_id = state.get("run_id", "unknown")
        audit = get_audit_logger(run_id)
        claim_verifications = _build_claim_verifications(
            completeness, sector_q, financial_q, issues,
            state.get("financial_data"), state.get("company_info"),
        )
        ver_summary = _build_verification_summary(claim_verifications)
        audit.quality_decision(
            score=score,
            retry_triggered=(feedback is not None),
            route="retry" if feedback else "end",
            issues=issues,
        )
        audit.validation_result(
            "quality_review",
            passed=(score >= 7),
            failures=issues,
        )
        logger.info(
            f"Claim verifications: {ver_summary.total_claims} total  "
            f"low_confidence={ver_summary.low_confidence_count}  "
            f"needs_escalation={ver_summary.needs_escalation()}"
        )

        return {
            "quality_review_result": review,
            "quality_feedback": feedback,
            "retry_count": retry_count + 1,
            "current_step": "review_done",
            "claim_verifications": [c.to_dict() for c in claim_verifications],
            "verification_summary": ver_summary.model_dump(),
        }
    except Exception as e:
        logger.error(f"Quality review failed: {e}", exc_info=True)
        return {
            "retry_count": state.get("retry_count", 0) + 1,
            "current_step": "review_done",
        }


def _build_claim_verifications(
    completeness: float,
    sector_q: float,
    financial_q: float,
    issues: list[str],
    financial_data=None,
    company_info=None,
) -> list[ClaimVerification]:
    """Derive per-claim verifications from section scores and known data.

    This is a structured interpretation of the LLM judge scores — each score
    becomes a typed claim with confidence derived from the 0-10 scale.
    We also add deterministic checks (e.g. balance sheet fields present).
    """
    claims: list[ClaimVerification] = []

    def score_to_confidence(score: float) -> float:
        return round(min(max(score / 10.0, 0.0), 1.0), 2)

    # Output 1: company info completeness claim
    claims.append(ClaimVerification(
        claim_text="Thông tin khách hàng đầy đủ (tên, MST, địa chỉ, cổ đông, đại diện pháp luật)",
        claim_type="completeness",
        confidence=score_to_confidence(completeness),
        source_reference="MD company info file",
        verified=(completeness >= 7),
        issues=[i for i in issues if any(k in i.lower() for k in ["thông tin", "mst", "cổ đông", "completeness"])],
    ))

    # Output 2: sector analysis claim
    claims.append(ClaimVerification(
        claim_text="Phân tích lĩnh vực kinh doanh có đủ nội dung và rủi ro ngành",
        claim_type="sector_claim",
        confidence=score_to_confidence(sector_q),
        source_reference="Web search / LLM knowledge",
        verified=(sector_q >= 7),
        issues=[i for i in issues if any(k in i.lower() for k in ["ngành", "sector", "rủi ro", "risk"])],
    ))

    # Output 3: financial analysis claim
    claims.append(ClaimVerification(
        claim_text="Phân tích tài chính có số liệu BCTC và chỉ số tài chính hợp lý",
        claim_type="financial_fact",
        confidence=score_to_confidence(financial_q),
        source_reference="PDF financial statements",
        verified=(financial_q >= 7),
        issues=[i for i in issues if any(k in i.lower() for k in ["tài chính", "financial", "ratio", "chỉ số"])],
    ))

    # Deterministic claim: balance sheet data present
    if financial_data is not None:
        statements = getattr(financial_data, "statements", {}) or {}
        has_assets = any(
            getattr(s, "total_assets", None) and getattr(s, "total_assets") > 0
            for s in statements.values()
        )
        claims.append(ClaimVerification(
            claim_text="Bảng cân đối kế toán có giá trị total_assets > 0",
            claim_type="financial_fact",
            confidence=0.95 if has_assets else 0.1,
            source_reference="Extracted from PDF via OCR",
            verified=has_assets,
            issues=[] if has_assets else ["total_assets = 0 or None across all years"],
        ))

    # Deterministic claim: company name present
    if company_info is not None:
        has_name = bool(getattr(company_info, "company_name", None))
        claims.append(ClaimVerification(
            claim_text="Tên công ty được xác định",
            claim_type="completeness",
            confidence=0.95 if has_name else 0.1,
            source_reference="MD company info file",
            verified=has_name,
            issues=[] if has_name else ["company_name is empty after extraction"],
        ))

    return claims


def _build_verification_summary(claims: list[ClaimVerification]) -> VerificationSummary:
    total = len(claims)
    verified = sum(1 for c in claims if c.verified)
    low_conf = sum(1 for c in claims if c.confidence < 0.5)
    unverified = total - verified
    avg_conf = sum(c.confidence for c in claims) / total if total > 0 else 0.0
    return VerificationSummary(
        layers_run=["llm_judge", "deterministic"],
        total_claims=total,
        verified_count=verified,
        low_confidence_count=low_conf,
        unverified_count=unverified,
        overall_confidence=round(avg_conf, 2),
    )


def _build_report_header(company_name: str, date: str) -> str:
    return f"""# GIẤY ĐỀ NGHỊ CẤP TÍN DỤNG

**Ngày lập:** {date}
**Khách hàng:** {company_name}

---"""
