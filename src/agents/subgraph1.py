from ..tools.company_info import read_md_company_info
from ..models.state import AgentState
from ..models.company import CompanyInfo
from ..utils.logger import get_logger, timed_node
from ..utils.circuit_breaker import CircuitBreaker
from ..utils.checkpoint import save_node_checkpoint
from ..utils.audit import get_audit_logger
from ..utils.validation import validate_company_info

logger = get_logger("subgraph1")
_breaker = CircuitBreaker()


@timed_node("extract_company_info")
def extract_company_info_node(state: AgentState) -> dict:
    """Node: Extract company information from markdown file."""
    md_path = state['md_company_info_path']
    logger.info(f"Reading company info from: {md_path}")

    try:
        raw_data = read_md_company_info(md_path)
        # llama-4-scout sometimes returns null for company_name; fall back to CLI arg
        if not raw_data.get("company_name"):
            raw_data["company_name"] = state.get("company_name", "")
        company_info = CompanyInfo(**raw_data)
        logger.info(f"Extracted company: {company_info.company_name}  tax_code={company_info.tax_code}")

        # Build section 1 markdown
        section_md = _build_company_section(company_info)
        logger.debug(f"Section 1 built — {len(section_md)} chars")

        # Circuit breaker: check critical fields before downstream nodes consume this
        cb_result = _breaker.check_company_info(company_info)
        run_id = state.get("run_id", "unknown")
        audit = get_audit_logger(run_id)

        if cb_result.tripped:
            audit.circuit_breaker_trip("extract_company_info", cb_result.reason)
            return {
                "errors": [f"[circuit_breaker] {cb_result.reason}"],
                "current_step": "circuit_breaker_trip"
            }
        if cb_result.warnings:
            audit.circuit_breaker_warn("extract_company_info", cb_result.warnings)

        # Cross-agent validation gate
        val_failures = validate_company_info(company_info)
        audit.validation_result("extract_company_info", len(val_failures) == 0, val_failures)

        # Checkpoint: save company_info so it can be reused on partial re-run
        save_node_checkpoint(run_id, "01_company_info", {
            "company_name": company_info.company_name,
            "tax_code": company_info.tax_code,
            "main_business": company_info.main_business,
        })
        audit.tool_call("save_node_checkpoint", node="01_company_info", run_id=run_id)

        return {
            "company_info": company_info,
            "section_1_company": section_md,
            "current_step": "company_info_done",
            "errors": [f"[circuit_breaker][warn] {w}" for w in cb_result.warnings],
        }
    except Exception as e:
        error_msg = f"Error in extract_company_info_node: {e}"
        logger.error(error_msg, exc_info=True)
        return {
            "errors": [error_msg],   # reducer appends to state.errors
            "current_step": "company_info_failed"
        }


def _build_company_section(info: CompanyInfo) -> str:
    """Build Section 'Thông tin Khách hàng' matching GIẤY ĐỀ NGHỊ CẤP TÍN DỤNG template."""
    lines = []
    lines.append("# Thông tin Khách hàng\n")

    # 1.1 — Thông tin Khách hàng đề nghị cấp tín dụng (Pháp nhân)
    lines.append("## 1.1 Thông tin Khách hàng đề nghị cấp tín dụng\n")
    lines.append("| Thông tin | Chi tiết |")
    lines.append("| --- | --- |")
    lines.append(f"| Tên Khách hàng | {info.company_name} |")
    if info.tax_code:
        lines.append(f"| Giấy CNĐKKD/CNĐKDN (MST) | {info.tax_code} |")
    if info.established_date:
        lines.append(f"| Ngày cấp | {info.established_date} |")
    if info.address:
        lines.append(f"| Địa chỉ trụ sở trên Giấy CNĐKDN | {info.address} |")
    if info.phone:
        lines.append(f"| Điện thoại | {info.phone} |")
    if info.email:
        lines.append(f"| Email | {info.email} |")
    if info.website:
        lines.append(f"| Website | {info.website} |")
    if info.main_business:
        lines.append(f"| Ngành nghề kinh doanh chính | {info.main_business} |")
    if info.charter_capital:
        lines.append(f"| Vốn điều lệ | {info.charter_capital} |")
    if info.stock_code:
        lines.append(f"| Mã chứng khoán | {info.stock_code} ({info.stock_exchange or 'N/A'}) |")
    lines.append("")

    if info.company_history:
        lines.append("**Quá trình hình thành và phát triển:**\n")
        lines.append(info.company_history)
        lines.append("")

    # 1.2 — Thông tin người đại diện (legal representative)
    lines.append("## 1.2 Thông tin về người đại diện đề nghị cấp tín dụng\n")
    lines.append("| Thông tin | Chi tiết |")
    lines.append("| --- | --- |")
    lines.append(f"| Họ và tên | {info.legal_representative or '……………………………….'} |")
    lines.append("")

    # Governance tables (HĐQT, BGĐ, BKS) — supporting info for 1.2
    if info.board_of_directors:
        lines.append("**Hội đồng quản trị:**\n")
        lines.append("| Họ và tên | Chức vụ | Tuổi |")
        lines.append("| --- | --- | --- |")
        for m in info.board_of_directors:
            lines.append(f"| {m.name} | {m.role} | {m.age or '-'} |")
        lines.append("")

    if info.management:
        lines.append("**Ban giám đốc:**\n")
        lines.append("| Họ và tên | Chức vụ | Tuổi |")
        lines.append("| --- | --- | --- |")
        for m in info.management:
            lines.append(f"| {m.name} | {m.role} | {m.age or '-'} |")
        lines.append("")

    if info.supervisory_board:
        lines.append("**Ban kiểm soát:**\n")
        lines.append("| Họ và tên | Chức vụ |")
        lines.append("| --- | --- |")
        for m in info.supervisory_board:
            lines.append(f"| {m.name} | {m.role} |")
        lines.append("")

    # 1.3 — Cơ cấu vốn góp và quan hệ tín dụng của thành viên/cổ đông
    if info.shareholders:
        lines.append("## 1.3 Cơ cấu vốn góp và quan hệ tín dụng của thành viên/cổ đông góp vốn\n")
        lines.append("| STT | Họ và tên | Tỷ lệ góp vốn (%) | Số cổ phiếu | Dư nợ/lịch sử tín dụng với VPB |")
        lines.append("| --- | --- | --- | --- | --- |")
        for i, sh in enumerate(info.shareholders, 1):
            shares_str = f"{sh.shares:,}" if sh.shares else "-"
            pct_str = f"{sh.percentage:.2f}%" if sh.percentage else "-"
            lines.append(f"| {i} | {sh.name} | {pct_str} | {shares_str} | ……………………. |")
        lines.append("")

    return '\n'.join(lines)
