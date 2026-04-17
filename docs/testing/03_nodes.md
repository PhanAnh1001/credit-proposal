# Test 03 — Test từng LangGraph Node riêng lẻ

**Prerequisite**: Test 02 phải pass trước.

Mỗi node được gọi **trực tiếp** với state giả — không cần chạy toàn bộ graph.
Chạy từ thư mục gốc của project sau khi đã `source .venv/bin/activate`.

> **Cách dùng**: Mở REPL với `python`, paste từng đoạn code vào.
> Hoặc lưu thành file `.py` và chạy `python ten_file.py`.

### Setup chung (paste vào đầu mỗi session REPL)

```bash
python
```

```python
import sys
sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv(".env")
```

---

## Node 3.1 — `extract_company_info_node` (Subgraph 1)

```python
from src.agents.subgraph1 import extract_company_info_node

state = {
    "md_company_info_path": "data/uploads/mst/general-information/md/mst-information.md",
    "errors": [],
    "messages": [],
}

result = extract_company_info_node(state)
print(f"  current_step  : {result.get('current_step')}")
print(f"  company_name  : {result.get('company_info').company_name if result.get('company_info') else None}")
print(f"  section_1 len : {len(result.get('section_1_company', '') or '')} chars")
print(f"  errors        : {result.get('errors', [])}")

assert result.get("current_step") == "company_info_done"
assert result.get("company_info") is not None
assert "I. THÔNG TIN CHUNG" in (result.get("section_1_company") or "")
print("PASS")
```

**Log mong đợi**:
```
[INFO] [subgraph1]  📋 START  →  extract_company_info
[INFO] [subgraph1]  Reading company info from: ...
[INFO] [subgraph1]  Extracted company: ...
[INFO] [subgraph1]  📋 END    ←  extract_company_info  [X.Xs]
```

---

## Node 3.2 — `analyze_sector_node` (Subgraph 2)

> Gọi LLM → ~10-30 giây

```python
from src.agents.subgraph2 import analyze_sector_node
from src.models.company import CompanyInfo

mock_company = CompanyInfo(
    company_name="Công ty Cổ phần Xây dựng MST",
    main_business="xây dựng hạ tầng giao thông",
    tax_code="0123456789",
)

state = {
    "company_info": mock_company,
    "errors": [],
    "messages": [],
}

result = analyze_sector_node(state)
print(f"  current_step  : {result.get('current_step')}")
print(f"  section_2 len : {len(result.get('section_2_sector', '') or '')} chars")

assert result.get("current_step") == "sector_done"
assert "II. THÔNG TIN LĨNH VỰC" in (result.get("section_2_sector") or "")
assert len(result.get("section_2_sector", "")) > 300
print("PASS")
```

---

## Node 3.3 — `analyze_financial_node` (Subgraph 3)

> Node nặng nhất — đọc PDF + gọi LLM nhiều lần → 2-8 phút cho 3 năm.

### Test đầy đủ (3 năm)

```python
from src.agents.subgraph3 import analyze_financial_node

state = {
    "pdf_dir_path": "data/uploads/mst/financial-statements/pdf",
    "company": "mst",
    "errors": [],
    "messages": [],
}

print("Đang xử lý PDF... (2-8 phút)")
result = analyze_financial_node(state)

print(f"  current_step      : {result.get('current_step')}")
print(f"  financial_data    : {result.get('financial_data') is not None}")
print(f"  section_3 len     : {len(result.get('section_3_financial', '') or '')} chars")
print(f"  errors            : {result.get('errors', [])}")

if result.get("current_step") == "financial_done":
    fin = result["financial_data"]
    for year, stmt in fin.statements.items():
        print(f"  Year {year}: total_assets={stmt.total_assets}  net_revenue={stmt.net_revenue}")
    print("PASS")
else:
    print(f"FAIL — step={result.get('current_step')}  errors={result.get('errors')}")
```

### Test nhanh (chỉ 1 năm)

```python
import tempfile, shutil
from pathlib import Path
from src.agents.subgraph3 import analyze_financial_node

with tempfile.TemporaryDirectory() as tmpdir:
    src_year = Path("data/uploads/mst/financial-statements/pdf/2024")
    shutil.copytree(src_year, Path(tmpdir) / "2024")
    state = {"pdf_dir_path": tmpdir, "company": "mst", "errors": [], "messages": []}
    result = analyze_financial_node(state)

print(f"  current_step : {result.get('current_step')}")
fin = result.get("financial_data")
if fin:
    print(f"  years        : {list(fin.statements.keys())}")
    print("PASS")
```

---

## Node 3.4 — `assemble_report_node` (Assembler)

> Không gọi LLM, chỉ ghép text + save file → < 3 giây.

```python
import tempfile, shutil, os
from src.agents.assembler import assemble_report_node
from src.models.company import CompanyInfo

with tempfile.TemporaryDirectory() as out_dir:
    state = {
        "company_info": CompanyInfo(company_name="Công ty Test MST", tax_code="0123456789"),
        "company_name": "Công ty Test MST",
        "section_1_company": "# I. THÔNG TIN CHUNG\n\n**Test content 1**\n",
        "section_2_sector": "# II. THÔNG TIN LĨNH VỰC\n\n**Test content 2**\n",
        "section_3_financial": "# III. PHÂN TÍCH TÀI CHÍNH\n\n**Test content 3**\n",
        "output_dir": out_dir,
        "errors": [],
        "messages": [],
    }

    result = assemble_report_node(state)

    assert result.get("current_step") == "completed"
    report = result.get("final_report_md", "")
    assert "TỜ TRÌNH" in report
    assert "I. THÔNG TIN CHUNG" in report
    assert "II. THÔNG TIN LĨNH VỰC" in report
    assert "III. PHÂN TÍCH TÀI CHÍNH" in report
    assert os.path.exists(os.path.join(out_dir, "credit-proposal.md"))

    docx_path = result.get("final_report_docx_path")
    if docx_path:
        assert os.path.exists(docx_path)
        print(f"  DOCX: {os.path.getsize(docx_path):,} bytes")

print("PASS")
```

---

## Node 3.5 — `quality_review_node`

> Gọi LLM → ~5-10 giây.

```python
from src.agents.assembler import quality_review_node

mock_report = """# TỜ TRÌNH ĐỀ NGHỊ CẤP TÍN DỤNG
**Ngân hàng:** VPBank  **Ngày lập:** 01/01/2025  **Khách hàng:** Công ty Test

# I. THÔNG TIN CHUNG
Vốn điều lệ: 100 tỷ đồng. Ngành: xây dựng hạ tầng.

# II. THÔNG TIN LĨNH VỰC
Ngành xây dựng hạ tầng có triển vọng tốt với nhiều dự án đầu tư công.

# III. PHÂN TÍCH TÀI CHÍNH
Doanh thu 2024: 500 tỷ đồng, tăng 20% so với 2023. ROE: 12%. Current Ratio: 1.5.
"""

state = {"final_report_md": mock_report, "errors": [], "messages": []}
result = quality_review_node(state)
print(f"  current_step: {result.get('current_step')}")
assert result.get("current_step") == "review_done"
print("PASS")
```

---

## Checklist Node Tests

| Node | Pass? | Thời gian | Ghi chú |
|------|-------|-----------|---------|
| `extract_company_info_node` | | < 10s | |
| `analyze_sector_node` | | < 30s | |
| `analyze_financial_node` | | < 8 phút | Node nặng nhất |
| `assemble_report_node` | | < 3s | |
| `quality_review_node` | | < 15s | |

Tất cả pass → tiến sang [Test 04](./04_pipeline.md).
