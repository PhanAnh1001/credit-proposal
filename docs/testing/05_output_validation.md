# Test 05 — Kiểm tra chất lượng Output

**Prerequisite**: Test 04 phải pass (có file output).

Test này kiểm tra **độ chính xác** của output so với dữ liệu gốc và **chất lượng** của phân tích.

---

## Bước 5.1 — Kiểm tra số liệu tài chính (spot-check)

So sánh số liệu trong output với file BCTC gốc bằng tay.

### Mở output và BCTC song song

```bash
# Xem output
grep -A5 "Tổng tài sản\|Doanh thu\|Lợi nhuận" data/outputs/mst/credit-analyst-memo.md

# Xem log để biết số liệu đã extract
grep "net_revenue\|total_assets" logs/run_$(date +%Y%m%d).log
```

### Script so sánh tự động

```bash
python << 'PYEOF'
import sys; sys.path.insert(0, ".")
import re

# Đọc output
with open("data/outputs/mst/credit-analyst-memo.md", encoding="utf-8") as f:
    report = f.read()

# Tìm tất cả số trong report (đơn vị tỷ, triệu)
numbers = re.findall(r'[\d,]+(?:\.\d+)?\s*(?:tỷ|triệu|%)', report)
print(f"Tìm thấy {len(numbers)} con số trong report")
for n in numbers[:20]:
    print(f"  {n}")
PYEOF
```

### Checklist kiểm tra tay

Mở file PDF BCTC năm 2024 và so sánh với output:

| Chỉ tiêu | Trong PDF (tỷ đ) | Trong Output (tỷ đ) | Chênh lệch |
|----------|-----------------|---------------------|------------|
| Tổng tài sản 2024 | | | |
| Tổng nợ phải trả 2024 | | | |
| Vốn CSH 2024 | | | |
| Doanh thu thuần 2024 | | | |
| Lợi nhuận sau thuế 2024 | | | |

**Kỳ vọng**: Chênh lệch < 5% cho mỗi chỉ tiêu.

---

## Bước 5.2 — Kiểm tra balance sheet integrity trong output

```bash
python << 'PYEOF'
import sys; sys.path.insert(0, ".")
from src.tools.ratio_calculator import validate_balance_sheet

# Điền số liệu từ bước 5.1 vào đây để kiểm tra
stmt_2024 = {
    "total_assets": None,     # Điền từ PDF
    "total_liabilities": None,
    "equity": None,
}

if all(stmt_2024.values()):
    errors = validate_balance_sheet(stmt_2024)
    if errors:
        print(f"  WARN: Balance sheet mất cân bằng: {errors}")
    else:
        print("  OK: Balance sheet cân bằng")
else:
    print("  SKIP: Chưa điền số liệu vào stmt_2024")
PYEOF
```

---

## Bước 5.3 — Kiểm tra hallucination cơ bản

Phần phân tích ngành (Section 2) có thể bị hallucination. Kiểm tra bằng script:

```bash
python << 'PYEOF'
import re

with open("data/outputs/mst/credit-analyst-memo.md", encoding="utf-8") as f:
    report = f.read()

section2_match = re.search(r"# II\.(.*?)# III\.", report, re.DOTALL)
section2 = section2_match.group(1) if section2_match else ""

print("=== Section 2 preview (500 chars) ===")
print(section2[:500])
print()
print("Các điểm cần kiểm tra thủ công:")
print("  1. Thông tin có liên quan đến ngành xây dựng không?")
print("  2. Số liệu % tăng trưởng có nguồn không?")
print("  3. Tên công ty/dự án có thật không?")
print()
print("=== Hallucination check ===")
red_flags = [
    (r"\d{4}.*%.*tăng",    "Số % tăng trưởng quá cụ thể không có nguồn"),
    (r"theo.*báo cáo",     "Trích dẫn báo cáo không rõ tên"),
    (r"ước tính khoảng",   "Ước tính không có cơ sở"),
]
for flag, desc in red_flags:
    matches = re.findall(flag, section2)
    status = "FLAG" if matches else "OK"
    print(f"  {status}  {desc}")
    if matches:
        print(f"         -> {matches[:2]}")
PYEOF
```

---

## Bước 5.4 — Kiểm tra format so với mẫu tờ trình

So sánh output với file mẫu:

```bash
# Xem mẫu tờ trình
head -50 data/templates/md/giay-de-nghi-vay-von.md

# So sánh cấu trúc sections
echo "=== Output sections ==="
grep "^# " data/outputs/mst/credit-analyst-memo.md

echo ""
echo "=== Mẫu sections ==="
grep "^# " data/templates/md/giay-de-nghi-vay-von.md
```

**Checklist format**:
- [ ] Header có "GIẤY ĐỀ NGHỊ CẤP TÍN DỤNG"
- [ ] Ngày lập có format `DD/MM/YYYY`
- [ ] Tên ngân hàng VPBank có
- [ ] Section "Thông tin Khách hàng" (1.1, 1.2, 1.3)
- [ ] Section "Thông tin đề nghị cấp tín dụng"
- [ ] Section "Thông tin tài sản đảm bảo"
- [ ] Section "Cam kết của Khách hàng"
- [ ] Phụ lục A: Thông tin lĩnh vực kinh doanh
- [ ] Phụ lục B: Phân tích tình hình tài chính
- [ ] Bảng dữ liệu dùng markdown table syntax
- [ ] Thuật ngữ tiếng Việt chuyên ngành ngân hàng

---

## Bước 5.5 — Kiểm tra DOCX file

Output DOCX phải giống hệt cấu trúc template (35 tables, 19 sections, không có phần mới append).
Phụ lục A/B chỉ xuất hiện trong `credit-analyst-memo.md`, không có trong DOCX.

```bash
python << 'PYEOF'
from docx import Document

TEMPLATE = "data/templates/docx/giay-de-nghi-vay-von.docx"
OUTPUT   = "data/outputs/mst/credit-proposal.docx"

tmpl = Document(TEMPLATE)
doc  = Document(OUTPUT)

tmpl_tables   = len(tmpl.tables)
tmpl_sections = len(tmpl.sections)
out_tables    = len(doc.tables)
out_sections  = len(doc.sections)

print(f"  Template : {tmpl_tables} tables, {tmpl_sections} sections")
print(f"  Output   : {out_tables} tables, {out_sections} sections")

# Cấu trúc phải khớp template — không được có bảng hay section mới
assert out_tables == tmpl_tables, \
    f"FAIL: tables {out_tables} != template {tmpl_tables} (phụ lục bị append thừa?)"
assert out_sections == tmpl_sections, \
    f"FAIL: sections {out_sections} != template {tmpl_sections}"

# Kiểm tra dữ liệu công ty đã được điền vào Table[1]
t1 = doc.tables[1]
customer_name = t1.rows[2].cells[1].text.strip()
print(f"  customer_name (Table[1] r2 c1): {customer_name!r}")
assert customer_name, "FAIL: customer_name trống — company info chưa được fill"

# Kiểm tra PHỤ LỤC 6 có số liệu tài chính
t29 = doc.tables[29]
revenue_n = t29.rows[1].cells[3].text.strip()
print(f"  revenue năm N (Table[29] r1 c3): {revenue_n!r}")
assert revenue_n, "FAIL: revenue trống — financial data chưa được fill"

# Kiểm tra DOCX không có Phụ lục A/B (chỉ trong MD)
full_text = " ".join(p.text for p in doc.paragraphs)
assert "Phụ lục A" not in full_text and "Phụ lục B" not in full_text, \
    "FAIL: Phụ lục A/B bị append vào DOCX — phải chỉ có trong credit-analyst-memo.md"

print("PASS")
PYEOF
```

---

## Bước 5.6 — Kiểm tra timing per node từ log

```bash
python << 'PYEOF'
import re
from datetime import date

log_file = f"logs/run_{date.today().strftime('%Y%m%d')}.log"
try:
    with open(log_file) as f:
        log = f.read()
except FileNotFoundError:
    print(f"Log file không tìm thấy: {log_file}")
    exit(1)

timing_pattern = r'\[(.*?)\]\s+(?:📋|🔍|📊|📄|✅) END.*?\[(\d+\.\d+)s\]'
timings = re.findall(timing_pattern, log)

print("=== Node Timing ===")
total = 0.0
for node, elapsed in timings:
    elapsed_f = float(elapsed)
    total += elapsed_f
    status = "OK" if elapsed_f < 300 else "SLOW"
    print(f"  {status}  {node}: {elapsed_f:.1f}s")

if total < 600:
    summary = "OK < 10 phút"
else:
    summary = f"WARN: {total/60:.1f} phút"
print(f"\n  Total: {total:.1f}s  ({summary})")
PYEOF
```

---

## Scorecard tổng hợp

Sau khi hoàn thành tất cả test, điền vào bảng:

| Tiêu chí | Target | Kết quả | Pass? |
|----------|--------|---------|-------|
| Số liệu tài chính chính xác | ≥ 95% | | |
| Balance sheet cân bằng | < 2% sai lệch | | |
| 3 sections có đủ nội dung | 100% | | |
| DOCX có bảng + heading | Có | | |
| Không có ERROR trong log | 0 errors | | |
| Latency tổng | < 10 phút | | |
| Section 2 không hallucinate số liệu | < 2 flags | | |

**Nếu tất cả pass**: Pipeline sẵn sàng demo.  
**Nếu có fail**: Quay lại test 02/03 để debug từng component.
