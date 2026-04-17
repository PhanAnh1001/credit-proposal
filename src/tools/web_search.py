"""Web search tool for industry/sector research using Tavily API."""
import os
from typing import Optional
from langchain_core.messages import HumanMessage, SystemMessage
from ..utils.llm import get_smart_llm, invoke_with_retry
from ..utils.logger import get_logger

logger = get_logger("web_search")


def _tavily_search(query: str, max_results: int = 5) -> list[dict]:
    """Run a Tavily search and return raw results.

    Falls back to an empty list if the TAVILY_API_KEY is not set or the
    request fails.
    """
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        logger.warning("TAVILY_API_KEY not set — skipping web search")
        return []

    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=api_key)
        logger.debug(f"Tavily query: '{query}'  max_results={max_results}")
        response = client.search(
            query=query,
            search_depth="basic",
            max_results=max_results,
            include_answer=True,
        )
        results = response.get("results", [])
        logger.debug(f"Tavily returned {len(results)} results")
        return results
    except Exception as e:
        logger.error(f"Tavily search failed: {e}", exc_info=True)
        return []


def _synthesize_sector_analysis(
    industry: str,
    company_name: str,
    search_results: list[dict],
    llm,
    extra_hint: str = "",
) -> str:
    """Use an LLM to synthesize search results into a structured sector analysis."""

    if search_results:
        context_parts = []
        for r in search_results:
            title = r.get("title", "")
            content = r.get("content", "")
            url = r.get("url", "")
            context_parts.append(f"### {title}\nURL: {url}\n{content}")
        context = "\n\n".join(context_parts)
    else:
        context = (
            f"Không có kết quả tìm kiếm. Sử dụng kiến thức nền về ngành {industry} tại Việt Nam."
        )

    system_prompt = """Bạn là chuyên gia phân tích ngành kinh tế Việt Nam.
Nhiệm vụ: Viết phần phân tích lĩnh vực kinh doanh cho tờ trình tín dụng ngân hàng.

Nguyên tắc:
1. Dựa trên thông tin tìm kiếm được (nếu có) hoặc kiến thức chuyên môn về ngành
2. Trình bày khách quan, chuyên nghiệp theo chuẩn văn bản ngân hàng Việt Nam
3. Phân tích CÂN BẰNG: phần cơ hội và phần rủi ro phải có độ dài tương đương
4. Phần rủi ro (2.3) PHẢI liệt kê ít nhất 4 loại rủi ro cụ thể — không được chỉ đề cập tên mà không mô tả
5. Sử dụng thuật ngữ chuyên ngành phù hợp
6. Cấu trúc rõ ràng với các tiêu đề phụ
"""

    user_prompt = f"""Ngành kinh doanh: {industry}
Công ty: {company_name}
{extra_hint}
Thông tin từ tìm kiếm web:
{context[:6000]}

Viết phân tích lĩnh vực kinh doanh với cấu trúc sau:

## 2.1 Tổng quan ngành
(Mô tả ngành kinh doanh, quy mô thị trường, vị trí trong nền kinh tế Việt Nam)

## 2.2 Xu hướng phát triển
(Triển vọng tăng trưởng, cơ hội thị trường, chính sách hỗ trợ của Nhà nước)

## 2.3 Các rủi ro chính
(BẮT BUỘC liệt kê ít nhất 4 rủi ro cụ thể, mỗi rủi ro có 1-2 câu mô tả:
1. Rủi ro thị trường/cung cầu (biến động giá, cạnh tranh)
2. Rủi ro quy định pháp lý (thay đổi chính sách, cấp phép)
3. Rủi ro vĩ mô (lãi suất, tỷ giá, lạm phát, chu kỳ kinh tế)
4. Rủi ro vận hành hoặc môi trường đặc thù ngành
Phải nêu rủi ro cụ thể — KHÔNG được chỉ mô tả cơ hội.)

## 2.4 Vị thế cạnh tranh
(Đánh giá chung về môi trường cạnh tranh trong ngành)
"""

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]

    response = invoke_with_retry(llm, messages)
    return response.content


def web_search_industry(
    industry: str,
    company_name: Optional[str] = None,
    extra_hint: str = "",
) -> str:
    """Research industry/sector information and return a structured Markdown analysis.

    Args:
        industry:    Industry or sector name (Vietnamese or English).
        company_name: Optional company name for context.
        extra_hint:  Optional improvement hint injected on retry (from quality_feedback).

    Returns:
        Markdown string with structured sector analysis (Vietnamese).
    """
    company_name = company_name or "công ty khách hàng"

    # Build search queries in Vietnamese for better local results
    queries = [
        f"ngành {industry} Việt Nam 2024 triển vọng tăng trưởng",
        f"rủi ro ngành {industry} Việt Nam 2024",
        f"thị trường {industry} Việt Nam hiện nay",
    ]

    all_results: list[dict] = []
    seen_urls: set[str] = set()

    for query in queries:
        results = _tavily_search(query, max_results=3)
        for r in results:
            url = r.get("url", "")
            if url not in seen_urls:
                seen_urls.add(url)
                all_results.append(r)
        if len(all_results) >= 9:
            break

    logger.info(f"Total unique search results: {len(all_results)} for industry='{industry}'")

    llm = get_smart_llm()
    logger.debug("Synthesizing sector analysis via LLM")
    analysis = _synthesize_sector_analysis(industry, company_name, all_results, llm,
                                           extra_hint=extra_hint)
    logger.info(f"Sector analysis synthesized — {len(analysis)} chars")

    return analysis
