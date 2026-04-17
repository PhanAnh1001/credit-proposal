# Test 04 — Test Pipeline End-to-End

**Prerequisite**: Test 03 phải pass trước.

Chạy toàn bộ pipeline từ input → output. Đây là test gần nhất với thực tế demo.
Chạy từ thư mục gốc sau khi đã `source .venv/bin/activate`.

---

## Bước 4.1 — Chuẩn bị

```bash
# Kiểm tra log directory (sẽ được tạo tự động khi chạy)
ls logs/ 2>/dev/null || echo "Logs dir chưa có, sẽ tạo tự động"

# Xóa output cũ nếu muốn test từ đầu
rm -rf data/outputs/mst/
```

---

## Bước 4.2 — Chạy pipeline qua CLI

```bash
python -m src.main \
    --company mst \
    --company-name "Công ty Cổ phần Xây dựng và Thương mại MST" \
    --base-dir data/uploads \
    --output-dir data/outputs/mst
```

**Thời gian dự kiến**: 3-8 phút (phụ thuộc tốc độ PDF extraction và Groq API).

### Quan sát log realtime

```bash
# Terminal mới, chạy song song với lệnh trên
tail -f logs/run_$(date +%Y%m%d).log
```

**Log flow mong đợi**:
```
[INFO] [graph]        ====...====
[INFO] [graph]        Starting credit proposal — company='...'
[INFO] [graph]        ====...====
[INFO] [subgraph1]    📋 START  →  extract_company_info
[INFO] [subgraph1]    Reading company info from: ...
[INFO] [subgraph1]    Extracted company: ...
[INFO] [subgraph1]    📋 END    ←  extract_company_info  [5.2s]
[INFO] [subgraph2]    🔍 START  →  analyze_sector
[INFO] [web_search]   Total unique search results: 7 for industry='...'
[INFO] [subgraph2]    🔍 END    ←  analyze_sector  [18.3s]
[INFO] [subgraph3]    📊 START  →  analyze_financial
[INFO] [pdf_extractor] Processing year 2022: ...
[INFO] [pdf_extractor] Strategy 1 (PyMuPDF) succeeded — 45000 chars
[INFO] [pdf_extractor] Year 2022 extracted  net_revenue=...  total_assets=...
...
[INFO] [subgraph3]    📊 END    ←  analyze_financial  [120.5s]
[INFO] [assembler]    📄 START  →  assemble_report
[INFO] [assembler]    Markdown saved → data/outputs/mst/credit-analyst-memo.md
[INFO] [assembler]    DOCX (form)   saved → data/outputs/mst/credit-proposal.docx
[INFO] [assembler]    DOCX (memo)   saved → data/outputs/mst/credit-analyst-memo.docx
[INFO] [assembler]    📄 END    ←  assemble_report  [2.1s]
[INFO] [assembler]    ✅ START  →  quality_review
[INFO] [assembler]    Quality score: 7/10 — ...
[INFO] [assembler]    ✅ END    ←  quality_review  [8.4s]
[INFO] [graph]        PIPELINE COMPLETE  [154.5s]
```

---

## Bước 4.3 — Kiểm tra output files

```bash
ls -la data/outputs/mst/
# Kỳ vọng:
#   credit-analyst-memo.md    (> 5KB)
#   credit-proposal.docx      (> 10KB) — form template filled
#   credit-analyst-memo.docx  (> 10KB) — analyst memo
```

```bash
python << 'PYEOF'
from pathlib import Path

md        = Path("data/outputs/mst/credit-analyst-memo.md")
docx_form = Path("data/outputs/mst/credit-proposal.docx")
docx_memo = Path("data/outputs/mst/credit-analyst-memo.docx")

assert md.exists(),        f"MD file không tồn tại: {md}"
assert docx_form.exists(), f"Form DOCX không tồn tại: {docx_form}"
assert docx_memo.exists(), f"Memo DOCX không tồn tại: {docx_memo}"

md_size        = md.stat().st_size
docx_form_size = docx_form.stat().st_size
docx_memo_size = docx_memo.stat().st_size
print(f"  MD file        : {md_size:,} bytes  {'OK' if md_size > 3000 else 'WARN: quá nhỏ'}")
print(f"  Form DOCX      : {docx_form_size:,} bytes  {'OK' if docx_form_size > 5000 else 'WARN: quá nhỏ'}")
print(f"  Memo DOCX      : {docx_memo_size:,} bytes  {'OK' if docx_memo_size > 5000 else 'WARN: quá nhỏ'}")
print("PASS")
PYEOF
```

---

## Bước 4.4 — Kiểm tra nội dung Markdown output

```bash
python << 'PYEOF'
with open("data/outputs/mst/credit-analyst-memo.md", encoding="utf-8") as f:
    report = f.read()

print(f"Total: {len(report)} chars")

checks = {
    "Header TỜ TRÌNH":             "TỜ TRÌNH" in report,
    "Section 1 - Thông tin chung": "I. THÔNG TIN CHUNG" in report,
    "Section 2 - Lĩnh vực KD":    "II. THÔNG TIN LĨNH VỰC" in report,
    "Section 3 - Tài chính":       "III. PHÂN TÍCH" in report,
    "Có bảng markdown":            report.count("|") > 10,
    "Có số liệu":                  any(c.isdigit() for c in report),
}

all_pass = True
for name, passed in checks.items():
    print(f"  {'OK' if passed else 'FAIL'}  {name}")
    if not passed:
        all_pass = False

print("PASS" if all_pass else "FAIL — xem log để debug")
PYEOF
```

---

## Bước 4.5 — Kiểm tra log errors

```bash
grep "\[ERROR" logs/run_$(date +%Y%m%d).log
```

**Kỳ vọng**: Không có dòng nào. Nếu có ERROR: xem context xung quanh để debug.

---

## Bước 4.6 — Regression test (company name khác)

```bash
python -m src.main \
    --company mst \
    --company-name "MST Construction Corp" \
    --base-dir data/uploads \
    --output-dir data/outputs/mst_v2
```

```bash
python << 'PYEOF'
with open("data/outputs/mst_v2/credit-analyst-memo.md") as f:
    content = f.read()
assert "MST Construction Corp" in content, "Tên công ty không được đưa vào report"
print("PASS — company name được dùng đúng")
PYEOF
```

---

## Kết quả mong đợi của Test 04

| Tiêu chí | Kỳ vọng |
|----------|---------|
| Pipeline hoàn thành không crash | Pass |
| MD file > 5KB | Pass |
| DOCX file > 5KB | Pass |
| 3 sections có trong output | Pass |
| Không có ERROR trong log | Pass |
| Latency tổng | < 10 phút |

Tất cả pass → tiến sang [Test 05](./05_output_validation.md).
