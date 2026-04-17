# Test 02 — Test từng Tool riêng lẻ

**Prerequisite**: Test 01 phải pass trước.

Mỗi tool được test **độc lập**, không phụ thuộc vào nhau.
Chạy từ thư mục gốc của project sau khi đã `source .venv/bin/activate`.

> **Cách dùng**: Mở REPL với `python`, rồi paste từng đoạn code vào.
> Hoặc lưu thành file `.py` và chạy `python ten_file.py`.

---

## Tool 2.1 — `read_md_company_info`

**Mục đích**: Đọc file Markdown thông tin công ty, dùng LLM extract thành Pydantic model.

### Mở REPL

```bash
python
```

### Chạy trong REPL

```python
import sys
sys.path.insert(0, ".")
from dotenv import load_dotenv
load_dotenv(".env")

from src.tools.company_info import read_md_company_info

md_path = "data/uploads/mst/general-information/md/mst-information.md"
result = read_md_company_info(md_path)

print("=== Kết quả ===")
for key, value in result.items():
    print(f"  {key}: {value!r}")
```

### Kiểm tra

```python
from src.models.company import CompanyInfo

info = CompanyInfo(**result)
print(f"\n  company_name   : {info.company_name!r}")
print(f"  tax_code       : {info.tax_code!r}")
print(f"  address        : {info.address!r}")
print(f"  legal_rep      : {info.legal_representative!r}")
print(f"  shareholders   : {len(info.shareholders or [])} người")
print(f"  board_members  : {len(info.board_of_directors or [])} người")
```

**Kỳ vọng**:
- `company_name` không rỗng
- `tax_code` có format `XXXXXXXXXX` (10 số)
- `shareholders` có ít nhất 1 cổ đông
- Không raise exception

---

## Tool 2.2 — `calculate_financial_ratios` + `validate_balance_sheet`

**Mục đích**: Test pure-Python ratio calculator — không cần LLM, không cần PDF, chạy tức thì.

### Chạy (heredoc, không cần mở REPL)

```bash
python << 'PYEOF'
import sys; sys.path.insert(0, ".")
from src.tools.ratio_calculator import calculate_financial_ratios, validate_balance_sheet

mock_statements = {
    2022: {
        "year": 2022,
        "total_assets": 100_000, "current_assets": 60_000,
        "cash_and_equivalents": 5_000, "short_term_receivables": 25_000,
        "inventories": 20_000, "non_current_assets": 40_000,
        "total_liabilities": 60_000, "current_liabilities": 40_000,
        "long_term_liabilities": 20_000, "equity": 40_000,
        "net_revenue": 80_000, "gross_profit": 20_000,
        "net_profit": 5_000, "cost_of_goods_sold": 60_000,
    },
    2023: {
        "year": 2023,
        "total_assets": 120_000, "current_assets": 72_000,
        "cash_and_equivalents": 6_000, "short_term_receivables": 30_000,
        "inventories": 24_000, "non_current_assets": 48_000,
        "total_liabilities": 70_000, "current_liabilities": 45_000,
        "long_term_liabilities": 25_000, "equity": 50_000,
        "net_revenue": 100_000, "gross_profit": 28_000,
        "net_profit": 8_000, "cost_of_goods_sold": 72_000,
    }
}

errors_ok = validate_balance_sheet(mock_statements[2022])
print(f"Balance sheet 2022 errors (kỳ vọng []): {errors_ok}")

bad = mock_statements[2022].copy(); bad["equity"] = 50_000
errors_bad = validate_balance_sheet(bad)
assert len(errors_bad) > 0, "Validation phải phát hiện sai!"
print(f"Balance sheet bad errors (phải có): {errors_bad}")

ratios = calculate_financial_ratios(mock_statements)
r22 = ratios[2022]
print(f"current_ratio 2022: {r22['current_ratio']} (kỳ vọng 1.5)")
print(f"roe 2022: {r22['roe']}")
r23 = ratios.get(2023, {})
print(f"revenue_growth 2023: {r23.get('revenue_growth_yoy')} (kỳ vọng ~25.0)")
print("PASS")
PYEOF
```

**Kỳ vọng**: In `PASS`, không raise exception. Thời gian: < 1 giây.

---

## Tool 2.3 — `web_search_industry`

**Mục đích**: Test Tavily search + LLM synthesis. Gọi LLM → tốn ~10-20 giây.

```bash
python << 'PYEOF'
import sys; sys.path.insert(0, ".")
from dotenv import load_dotenv; load_dotenv(".env")
from src.tools.web_search import web_search_industry

result = web_search_industry(
    industry="xây dựng hạ tầng giao thông",
    company_name="Công ty Cổ phần MST"
)
print(f"Kết quả: {len(result)} chars")
print("--- Preview ---")
print(result[:300])

required = ["2.1", "2.2", "2.3", "2.4"]
for sec in required:
    status = "OK" if sec in result else "WARN"
    print(f"  {status}  Section {sec}")

assert len(result) > 500
print("PASS")
PYEOF
```

**Nếu TAVILY_API_KEY không set**: Log hiện `TAVILY_API_KEY not set`, LLM dùng knowledge nội bộ — output vẫn phải có cấu trúc.

---

## Tool 2.4 — `extract_pdf_financial_tables` (test 1 năm)

**Mục đích**: Test PDF extraction pipeline. Gọi LLM + đọc PDF → tốn 1-3 phút.

```bash
python << 'PYEOF'
import sys, tempfile, shutil; sys.path.insert(0, ".")
from pathlib import Path
from dotenv import load_dotenv; load_dotenv(".env")
from src.tools.pdf_extractor import extract_pdf_financial_tables

with tempfile.TemporaryDirectory() as tmpdir:
    src = Path("data/uploads/mst/financial-statements/pdf/2024")
    shutil.copytree(src, Path(tmpdir) / "2024")
    result = extract_pdf_financial_tables(tmpdir, company="mst")

print(f"Years extracted: {list(result.keys())}")
assert 2024 in result, "Năm 2024 phải được extract"
d = result[2024]
for field in ["total_assets", "equity", "net_revenue", "net_profit"]:
    v = d.get(field)
    print(f"  {field}: {v}")

ta = d.get("total_assets", 0) or 0
li = d.get("total_liabilities", 0) or 0
eq = d.get("equity", 0) or 0
if ta and li and eq:
    diff = abs(ta - (li + eq)) / ta * 100
    print(f"Balance sheet diff: {diff:.2f}%  ({'OK' if diff < 5 else 'WARN'})")
print("PASS")
PYEOF
```

**Kỳ vọng**: `2024` có trong kết quả, `total_assets` > 0.

---

## Checklist Tool Tests

| Tool | Pass? | Ghi chú |
|------|-------|---------|
| `read_md_company_info` | | |
| `validate_balance_sheet` | | |
| `calculate_financial_ratios` | | |
| `web_search_industry` | | |
| `extract_pdf_financial_tables` | | |

Tất cả pass → tiến sang [Test 03](./03_nodes.md).
