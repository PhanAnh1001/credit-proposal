import json
from ..tools.pdf_extractor import extract_pdf_financial_tables
from ..tools.ratio_calculator import calculate_financial_ratios, validate_balance_sheet
from ..models.state import AgentState
from ..models.financial import FinancialData, FinancialStatement, FinancialRatios
from ..utils.llm import get_financial_llm
from ..utils.logger import get_logger, timed_node
from ..utils.ocr_cache import OcrCache
from ..utils.circuit_breaker import CircuitBreaker
from ..utils.checkpoint import save_node_checkpoint
from ..utils.audit import get_audit_logger
from ..utils.validation import validate_financial_output
from langchain_core.messages import HumanMessage, SystemMessage

_cache = OcrCache()
_breaker = CircuitBreaker()

logger = get_logger("subgraph3")


@timed_node("analyze_financial")
def analyze_financial_node(state: AgentState) -> dict:
    """Node: Extract and analyze financial statements from PDF files."""
    pdf_dir = state['pdf_dir_path']
    logger.info(f"PDF directory: {pdf_dir}")

    try:
        # Step 1: Extract financial data from PDFs
        logger.info("Step 1/5 — Extracting financial tables from PDFs")
        company = state.get("company", "unknown")
        raw_statements = extract_pdf_financial_tables(pdf_dir, company=company)

        if not raw_statements:
            logger.error("No financial data extracted from PDFs")
            return {
                "errors": ["No financial data extracted"],
                "current_step": "financial_failed"
            }

        logger.info(f"Extracted data for years: {sorted(raw_statements.keys())}")

        # Step 2: Validate balance sheet integrity
        logger.info("Step 2/5 — Validating balance sheet integrity")
        validation_warnings = []
        for year, stmt in raw_statements.items():
            errors = validate_balance_sheet(stmt)
            if errors:
                logger.warning(f"Year {year} balance sheet warnings: {errors}")
            validation_warnings.extend([f"Year {year}: {e}" for e in errors])

        # Step 3: Calculate ratios and persist to cache
        logger.info("Step 3/5 — Calculating financial ratios")
        ratios_data = calculate_financial_ratios(raw_statements)
        logger.debug(f"Ratios computed for years: {sorted(ratios_data.keys())}")
        for yr, ratios_dict in ratios_data.items():
            _cache.save_ratios(company, yr, ratios_dict)

        # Step 4: Build FinancialData model
        logger.info("Step 4/5 — Building FinancialData model")
        statements = {}
        for year, stmt_dict in raw_statements.items():
            statements[year] = FinancialStatement(**stmt_dict)

        ratios = {}
        for year, ratio_dict in ratios_data.items():
            ratios[year] = FinancialRatios(**ratio_dict)

        financial_data = FinancialData(statements=statements, ratios=ratios)

        # Circuit breaker: check for anomalous financial data before generating narrative
        run_id = state.get("run_id", "unknown")
        audit = get_audit_logger(run_id)
        cb_result = _breaker.check_financial(financial_data, list(statements.keys()))
        if cb_result.tripped:
            audit.circuit_breaker_trip("analyze_financial", cb_result.reason)
            return {
                "errors": [f"[circuit_breaker] {cb_result.reason}"],
                "current_step": "circuit_breaker_trip"
            }
        if cb_result.warnings:
            audit.circuit_breaker_warn("analyze_financial", cb_result.warnings)
        for w in cb_result.warnings:
            validation_warnings.append(w)

        # Cross-agent validation gate
        sector = getattr(state.get("company_info"), "main_business", None)
        val_failures = validate_financial_output(financial_data, sector)
        audit.validation_result("analyze_financial", len(val_failures) == 0, val_failures)
        # Append validation failures as warnings (non-fatal — assembler handles missing data)
        for vf in val_failures:
            validation_warnings.append(f"[validation] {vf}")

        # Checkpoint: save years + key numbers for partial re-run
        years_summary = {
            str(yr): {
                "total_assets": getattr(statements.get(yr), "total_assets", None),
                "net_revenue": getattr(statements.get(yr), "net_revenue", None),
            }
            for yr in sorted(statements.keys())
        }
        save_node_checkpoint(run_id, "02_financial_data", {"years": years_summary})
        audit.tool_call("save_node_checkpoint", node="02_financial_data", run_id=run_id)

        # Step 5: Generate LLM financial analysis (with quality_feedback on retry)
        quality_feedback = state.get("quality_feedback")
        if quality_feedback:
            logger.info(f"Retry mode — applying quality feedback: {quality_feedback[:80]!r}")
        logger.info("Step 5/5 — Generating LLM financial analysis narrative")
        section_md = _build_financial_section(financial_data, validation_warnings,
                                              quality_feedback=quality_feedback)
        logger.info(f"Section 3 built — {len(section_md)} chars")

        return {
            "financial_data": financial_data,
            "section_3_financial": section_md,
            "current_step": "financial_done"
        }
    except Exception as e:
        error_msg = f"Error in analyze_financial_node: {e}"
        logger.error(error_msg, exc_info=True)
        return {
            "errors": [error_msg],   # reducer appends to state.errors
            "current_step": "financial_failed"
        }


def _format_number(value, unit="triệu đồng"):
    """Format financial number for display."""
    if value is None:
        return "N/A"
    if abs(value) >= 1000:
        return f"{value/1000:,.1f} tỷ đồng"
    return f"{value:,.1f} {unit}"


def _format_pct(value):
    """Format percentage."""
    if value is None:
        return "N/A"
    return f"{value:.2f}%"


def _build_financial_section(
    financial_data: FinancialData,
    warnings: list[str],
    quality_feedback: str | None = None,
) -> str:
    """Build Section 3 markdown from FinancialData using LLM analysis.

    Uses chain-of-thought prompting: ask the LLM to reason step-by-step through
    each table before writing the narrative.  On retry, `quality_feedback` injects
    specific improvement hints from the quality reviewer into the user prompt.
    """
    llm = get_financial_llm()

    years = sorted(financial_data.statements.keys())

    # Build data summary for LLM
    data_summary = {}
    for year in years:
        s = financial_data.statements[year]
        r = financial_data.ratios.get(year)
        data_summary[year] = {
            "statement": s.model_dump(exclude_none=True),
            "ratios": r.model_dump(exclude_none=True) if r else {}
        }

    # Chain-of-thought system prompt: explicit reasoning steps before writing
    system_prompt = """Bạn là chuyên viên tín dụng ngân hàng Việt Nam viết tờ trình thẩm định tín dụng.

Quy trình phân tích — thực hiện TUẦN TỰ từng bước:
Bước 1: Đọc số liệu CĐKT từng năm, xác định xu hướng tổng tài sản, nợ, vốn CSH.
Bước 2: Đọc số liệu KQKD từng năm, xác định xu hướng doanh thu, lợi nhuận, biên lợi nhuận và cơ cấu chi phí.
Bước 3: Đọc các chỉ số tài chính đã tính sẵn (current_ratio, quick_ratio, ROE, ROA, net_profit_margin, gross_profit_margin, debt_to_equity, debt_to_assets, revenue_growth_yoy), so sánh từng chỉ số với ngưỡng TB ngành.
Bước 4: Nếu có cảnh báo mâu thuẫn số liệu (tổng TS ≠ Nợ + VCSH), nêu rõ trong nhận xét.
Bước 5: Tổng hợp — xác định điểm mạnh (ít nhất 2), điểm yếu (ít nhất 2), rủi ro tín dụng chính.
Bước 6: Viết phân tích theo cấu trúc yêu cầu, trích dẫn số liệu cụ thể cho mọi nhận định.

Ràng buộc tuyệt đối:
- CHỈ dùng số liệu được cung cấp trong JSON, không bịa thêm số
- Mọi nhận định PHẢI kèm số liệu dẫn chứng (ví dụ: "ROE giảm từ 8,68% xuống 2,02%")
- Ngôn ngữ chuyên nghiệp, thuật ngữ ngân hàng Việt Nam
- So sánh tối thiểu 2 năm liên tiếp cho mỗi chỉ tiêu
- KHÔNG bỏ trống bất kỳ chỉ số nào trong bảng 3.3
"""

    feedback_block = ""
    if quality_feedback:
        feedback_block = f"\n⚠️ YÊU CẦU CẢI THIỆN TỪ REVIEWER:\n{quality_feedback}\n"

    year_cols = " | ".join(str(y) for y in years)
    user_prompt = f"""Số liệu tài chính (JSON, đơn vị: triệu đồng):
{json.dumps(data_summary, ensure_ascii=False, indent=2)}
{feedback_block}
Viết phần phân tích tài chính theo cấu trúc:

## 3.1 Bảng cân đối kế toán
*(Đơn vị: triệu đồng)*
(Bảng Markdown tổng hợp CĐKT các năm với cột % thay đổi YoY:
Các hàng: Tổng tài sản | TSNH | TSDH | Tổng nợ phải trả | Nợ ngắn hạn | Nợ dài hạn | Vốn chủ sở hữu
Sau bảng: nhận xét xu hướng 2-3 câu CÓ SỐ DẪN CHỨNG CỤ THỂ từ bảng.)

## 3.2 Kết quả hoạt động kinh doanh
*(Đơn vị: triệu đồng)*
(Bảng Markdown tổng hợp KQKD các năm với cột % thay đổi YoY:
Các hàng: Doanh thu thuần | Giá vốn hàng bán | Lợi nhuận gộp | LN trước thuế | LN sau thuế
Sau bảng: nhận xét xu hướng 2-3 câu CÓ SỐ DẪN CHỨNG; nếu doanh thu tăng nhưng lợi nhuận giảm (hoặc ngược lại) phải phân tích nguyên nhân qua cơ cấu chi phí và biên lợi nhuận.)

## 3.3 Các chỉ số tài chính chủ yếu
(Bảng Markdown với đầy đủ 9 chỉ số sau — lấy GIÁ TRỊ THỰC TẾ từ trường "ratios" trong JSON; KHÔNG bỏ trống hàng nào:

| Chỉ số | {year_cols} | Ngưỡng TB ngành | Đánh giá |
|--------|{"|".join(["---"] * (len(years) + 2))}|
| Thanh toán hiện hành (current_ratio) | ... | ≥ 1,5 | ... |
| Thanh toán nhanh (quick_ratio) | ... | ≥ 1,0 | ... |
| ROE (%) | ... | ≥ 10% | ... |
| ROA (%) | ... | ≥ 5% | ... |
| Biên LN ròng — net_profit_margin (%) | ... | ≥ 5% | ... |
| Biên LN gộp — gross_profit_margin (%) | ... | tuỳ ngành | ... |
| Nợ/VCSH — debt_to_equity | ... | ≤ 1,5 | ... |
| Nợ/Tổng TS — debt_to_assets (%) | ... | ≤ 60% | ... |
| Tăng trưởng DT YoY — revenue_growth_yoy (%) | ... | — | ... |

Sau bảng: nhận xét tổng thể 3-4 câu về khả năng thanh toán, khả năng sinh lời, mức độ đòn bẩy tài chính — MỖI NHẬN XÉT phải trích dẫn số liệu cụ thể từ bảng trên.)

## 3.4 Đánh giá tổng thể
(Tóm tắt CÓ SỐ CỤ THỂ từ các bảng trên: 2 điểm mạnh, 2 điểm yếu, rủi ro tín dụng chính — mỗi điểm 1-2 câu.)
"""

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt)
    ]

    response = llm.invoke(messages)
    analysis_text = response.content

    # Build the complete section
    lines = ["# Phụ lục B: Phân tích tình hình tài chính\n"]

    if warnings:
        lines.append("**Lưu ý về dữ liệu:**")
        for w in warnings:
            lines.append(f"- {w}")
        lines.append("")

    lines.append(analysis_text)

    return '\n'.join(lines)
