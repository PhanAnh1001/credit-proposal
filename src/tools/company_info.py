import json
from pathlib import Path
from json_repair import repair_json
from langchain_core.messages import HumanMessage, SystemMessage
from ..utils.llm import get_medium_llm, strip_llm_json

# ── Few-shot example ──────────────────────────────────────────────────────────
# One concrete example helps the LLM understand exactly what to extract and how
# to handle edge cases (age as string, null for missing fields, nested lists).
_FEW_SHOT_EXAMPLE = """
VÍ DỤ minh hoạ:

INPUT:
Công ty Cổ phần Xây dựng Hưng Phát, MST: 0312345678, thành lập ngày 01/03/2015
của Sở Kế hoạch và Đầu tư TP. Hồ Chí Minh.
Địa chỉ: 45 Nguyễn Huệ, Q.1, TP.HCM. ĐT: 028 38221234. Email: info@hungphat.vn
Vốn điều lệ: 50 tỷ đồng. Ngành nghề: Xây dựng công trình dân dụng.
Hội đồng quản trị: Ông Trần Văn Bình – Chủ tịch HĐQT (48 tuổi).
Tổng Giám đốc: Bà Nguyễn Thị Lan (42 tuổi).
Cổ đông: Trần Văn Bình 35%, Nguyễn Thị Lan 20% (tính đến 31/12/2024).

OUTPUT JSON:
{
  "company_name": "Công ty Cổ phần Xây dựng Hưng Phát",
  "tax_code": "0312345678",
  "address": "45 Nguyễn Huệ, Q.1, TP.HCM",
  "phone": "028 38221234",
  "email": "info@hungphat.vn",
  "website": null,
  "charter_capital": "50 tỷ đồng",
  "main_business": "Xây dựng công trình dân dụng",
  "stock_code": null,
  "stock_exchange": null,
  "established_date": "01/03/2015",
  "registration_authority": "Sở Kế hoạch và Đầu tư TP. Hồ Chí Minh",
  "legal_representative": "Trần Văn Bình",
  "company_history": null,
  "board_of_directors": [{"name": "Trần Văn Bình", "role": "Chủ tịch HĐQT", "age": "48"}],
  "supervisory_board": [],
  "management": [{"name": "Nguyễn Thị Lan", "role": "Tổng Giám đốc", "age": "42"}],
  "shareholders": [
    {"name": "Trần Văn Bình", "shares": null, "percentage": 35.0, "as_of_date": "31/12/2024"},
    {"name": "Nguyễn Thị Lan",  "shares": null, "percentage": 20.0, "as_of_date": "31/12/2024"}
  ]
}
"""


def read_md_company_info(md_file_path: str) -> dict:
    """Read markdown company info file and extract structured fields via LLM.

    Uses few-shot prompting (one concrete example) to improve extraction accuracy,
    particularly for edge cases like age type coercion, null handling, and
    nested board/shareholder lists.

    Args:
        md_file_path: Absolute path to the .md file (e.g. mst-information.md)

    Returns:
        dict matching CompanyInfo schema fields.
    """
    content = Path(md_file_path).read_text(encoding="utf-8")

    llm = get_medium_llm()

    system_prompt = f"""Bạn là chuyên gia phân tích thông tin doanh nghiệp.
Nhiệm vụ: Đọc file thông tin công ty và trích xuất dữ liệu có cấu trúc.
Trả về JSON với các trường sau (dùng null nếu không tìm thấy):
{{
  "company_name": "...",
  "tax_code": "...",
  "address": "...",
  "phone": "...",
  "email": "...",
  "website": "...",
  "charter_capital": "...",
  "main_business": "...",
  "stock_code": "...",
  "stock_exchange": "...",
  "established_date": "DD/MM/YYYY",
  "registration_authority": "...",
  "legal_representative": "...",
  "company_history": "...",
  "board_of_directors": [{{"name": "...", "role": "...", "age": "..."}}],
  "supervisory_board": [{{"name": "...", "role": "...", "age": "..."}}],
  "management": [{{"name": "...", "role": "...", "age": "..."}}],
  "shareholders": [{{"name": "...", "shares": 0, "percentage": 0.0, "as_of_date": "..."}}]
}}

Lưu ý quan trọng:
- age luôn là chuỗi (string) hoặc null, KHÔNG phải số nguyên
- percentage là số thực (float), ví dụ 5.55
- Nếu không có thông tin → dùng null (không bỏ trống)
- Chỉ trả về JSON thuần túy, không markdown, không giải thích

{_FEW_SHOT_EXAMPLE}"""

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=f"Thông tin công ty cần trích xuất:\n\n{content}"),
    ]

    response = llm.invoke(messages)
    raw = strip_llm_json(response.content)

    # Skip CoT prose before '{' (llama-4-scout writes reasoning before JSON)
    brace_pos = raw.find("{")
    if brace_pos > 0:
        raw = raw[brace_pos:]

    # repair_json handles both truncated output (token limit hit) and trailing
    # commentary — returns a valid JSON string even when input is incomplete
    result = json.loads(repair_json(raw.strip()))
    return result
