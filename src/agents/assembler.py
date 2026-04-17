import json
import os
from pathlib import Path
from datetime import datetime
from ..models.state import AgentState
from ..utils.llm import get_judge_llm, strip_llm_json, invoke_with_retry
from ..utils.docx_template import render_analyst_memo, render_from_template
from ..utils.logger import get_logger, timed_node
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

    return {
        "final_report_md": full_report,
        "final_report_docx_path": docx_path,
        "final_report_memo_docx_path": memo_docx_path,
        "current_step": "completed"
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
            "Tờ trình phân tích tín dụng (trích đại diện từng phần):\n\n"
            + "\n\n".join([
                (state.get("section_1_company") or "")[:1500],
                (state.get("section_2_sector")  or "")[:1500],
                (state.get("section_3_financial") or "")[:2000],
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
        # Identifies the weakest section and summarises the top issues into
        # a single hint string that subgraph2/3 will inject into their prompts.
        feedback: str | None = None
        retry_count = state.get("retry_count", 0)
        if score < 7 and retry_count == 0 and issues:
            weak_section = "phân tích tài chính" if financial_q <= sector_q else "phân tích ngành"
            top_issues = "; ".join(issues[:3])
            feedback = f"Cải thiện {weak_section}. Vấn đề cần sửa: {top_issues}"
            logger.info(f"Quality feedback generated for retry: {feedback!r}")

        return {
            "quality_review_result": review,
            "quality_feedback": feedback,
            "retry_count": retry_count + 1,
            "current_step": "review_done",
        }
    except Exception as e:
        logger.error(f"Quality review failed: {e}", exc_info=True)
        return {
            "retry_count": state.get("retry_count", 0) + 1,
            "current_step": "review_done",
        }


def _build_report_header(company_name: str, date: str) -> str:
    return f"""# GIẤY ĐỀ NGHỊ CẤP TÍN DỤNG

**Ngày lập:** {date}
**Khách hàng:** {company_name}

---"""
