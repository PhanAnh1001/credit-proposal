from ..tools.web_search import web_search_industry
from ..models.state import AgentState
from ..utils.logger import get_logger, timed_node

logger = get_logger("subgraph2")


@timed_node("analyze_sector")
def analyze_sector_node(state: AgentState) -> dict:
    """Node: Research and analyze sector/industry information."""
    company_info = state.get('company_info')
    if not company_info:
        logger.warning("No company_info in state — skipping sector analysis")
        return {
            "errors": ["No company_info available for sector analysis"],
            "current_step": "sector_failed"
        }

    industry = company_info.main_business or "xây dựng giao thông hạ tầng"
    company_name = company_info.company_name

    # On retry, quality_feedback carries specific improvement hints from the reviewer.
    quality_feedback = state.get("quality_feedback")
    retry_hint = f"\n\nYÊU CẦU CẢI THIỆN: {quality_feedback}" if quality_feedback else ""
    if quality_feedback:
        logger.info(f"Retry mode — applying quality feedback: {quality_feedback[:80]!r}")

    logger.info(f"Researching industry: '{industry}'  company='{company_name}'")

    try:
        sector_analysis = web_search_industry(industry, company_name, extra_hint=retry_hint)
        logger.info(f"Sector analysis complete — {len(sector_analysis)} chars")

        section_md = "# Phụ lục A: Thông tin lĩnh vực kinh doanh\n\n" + sector_analysis

        return {
            "sector_info": {"analysis": sector_analysis, "industry": industry},
            "section_2_sector": section_md,
            "current_step": "sector_done"
        }
    except Exception as e:
        error_msg = f"Error in analyze_sector_node: {e}"
        logger.error(error_msg, exc_info=True)
        return {
            "errors": [error_msg],   # reducer appends to state.errors
            "current_step": "sector_failed"
        }
