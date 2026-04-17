# Test 06 — Kiểm tra DOCX Output Cell-by-Cell

**Prerequisite**: Test 04 phải pass (có file `data/outputs/mst/credit-proposal.docx`).

Test này kiểm tra **từng cell cụ thể** trong DOCX output đã được fill đúng theo
mapping trong `docs/requirements/human_mapping.md`.

Các test trong `05_output_validation.md` Bước 5.5 kiểm tra cấu trúc (tables/sections count).
File này kiểm tra **nội dung** từng cell.

---

## Bước 6.1 — Load DOCX và in tổng quan

```bash
python << 'PYEOF'
from docx import Document

OUTPUT   = "data/outputs/mst/credit-proposal.docx"
TEMPLATE = "data/templates/docx/giay-de-nghi-vay-von.docx"

doc  = Document(OUTPUT)
tmpl = Document(TEMPLATE)

print(f"Output   : {len(doc.tables)} tables, {len(doc.sections)} sections")
print(f"Template : {len(tmpl.tables)} tables, {len(tmpl.sections)} sections")
assert len(doc.tables) == len(tmpl.tables), \
    f"FAIL: table count mismatch {len(doc.tables)} vs {len(tmpl.tables)}"
print("  Structure: OK")
PYEOF
```

---

## Bước 6.2 — Thông tin Khách hàng (Table[1])

Mapping: `docs/requirements/human_mapping.md` Mục 1.1

```bash
python << 'PYEOF'
from docx import Document

doc = Document("data/outputs/mst/credit-proposal.docx")
t1  = doc.tables[1]

results = []

def check(label, value, required=True):
    status = "OK" if value else ("FAIL" if required else "SKIP")
    results.append((status, label, value))

# r2 c1: Tên Khách hàng
check("customer_name       (r2 c1)", t1.rows[2].cells[1].text.strip())

# r3 c0: MST appended sau label
check("tax_code            (r3 c0)", t1.rows[3].cells[0].text.strip())

# r4 c1: Địa chỉ trụ sở
check("registered_address  (r4 c1)", t1.rows[4].cells[1].text.strip())

# r5 c1: Địa chỉ giao dịch
check("current_address     (r5 c1)", t1.rows[5].cells[1].text.strip())

# r6 c1: Điện thoại
check("phone               (r6 c1)", t1.rows[6].cells[1].text.strip())

# r7 c1: Ngành nghề KD chính
check("main_business       (r7 c1)", t1.rows[7].cells[1].text.strip())

# r9 c1: Vốn điều lệ
check("charter_capital     (r9 c1)", t1.rows[9].cells[1].text.strip())

# r10 c1: Vốn thực góp = equity (human_mapping note #1)
vot_thuc_gop = t1.rows[10].cells[1].text.strip()
check("vot_thuc_gop        (r10 c1)", vot_thuc_gop, required=False)
if vot_thuc_gop and not any(u in vot_thuc_gop for u in ["tỷ", "triệu"]):
    results.append(("WARN", "vot_thuc_gop format", f"'{vot_thuc_gop}' — kỳ vọng có 'tỷ' hoặc 'triệu'"))

print("=== Table[1] — Thông tin Khách hàng ===")
for status, label, value in results:
    val_preview = repr(value[:50]) if value else "''"
    print(f"  {status:4}  {label}  →  {val_preview}")

fails = [r for r in results if r[0] == "FAIL"]
if fails:
    raise AssertionError(f"{len(fails)} field(s) trống")
print("PASS")
PYEOF
```

---

## Bước 6.3 — Người đại diện + HĐQT/BGĐ/BKS (Table[2])

```bash
python << 'PYEOF'
from docx import Document

doc = Document("data/outputs/mst/credit-proposal.docx")
t2  = doc.tables[2]

# r14 c1: Người đại diện pháp luật
legal_rep = t2.rows[14].cells[1].text.strip()
print(f"  legal_representative (r14 c1): {legal_rep!r}")
assert legal_rep, "FAIL: legal_representative trống"

# HĐQT/BGĐ/BKS được inject thêm rows sau r16
total_rows = len(t2.rows)
print(f"  Table[2] total rows: {total_rows} (template = 17, expected > 17 nếu có board data)")
if total_rows <= 17:
    print("  WARN: không có rows HĐQT/BGĐ/BKS injected — board_of_directors/management có thể trống")
else:
    injected = total_rows - 17
    print(f"  Board rows injected: {injected}")
    # In ra các rows được inject
    for i, row in enumerate(t2.rows[17:], start=17):
        cells = [c.text.strip() for c in row.cells]
        # Deduplicate merged cells
        seen, dedup = set(), []
        for c in row.cells:
            if id(c) not in seen:
                seen.add(id(c))
                dedup.append(c.text.strip())
        if any(dedup):
            print(f"    r{i}: {dedup}")

print("PASS")
PYEOF
```

---

## Bước 6.4 — Cổ đông 1.3 (Table[3], rows 10–12)

```bash
python << 'PYEOF'
from docx import Document

doc = Document("data/outputs/mst/credit-proposal.docx")
t3  = doc.tables[3]

print("=== Table[3] — Cổ đông (rows 10–12) ===")
filled = 0
for i in range(3):
    row = t3.rows[10 + i]
    stt  = row.cells[0].text.strip()
    name = row.cells[1].text.strip()
    pct  = row.cells[5].text.strip()
    print(f"  r{10+i}: STT={stt!r}  name={name!r}  tỷ_lệ={pct!r}")
    if name:
        filled += 1
        if pct and "%" not in pct:
            print(f"    WARN: tỷ lệ '{pct}' không có ký tự '%'")

assert filled >= 1, "FAIL: không có cổ đông nào được fill vào Table[3]"
print(f"  Cổ đông filled: {filled}/3")
print("PASS")
PYEOF
```

---

## Bước 6.5 — PHỤ LỤC 1: Thành viên góp vốn chính (Table[14] + Table[15])

PHỤ LỤC 1 là form label|value. Section cá nhân: Table[14] r15-r16, Table[15] r5.

```bash
python << 'PYEOF'
from docx import Document

doc = Document("data/outputs/mst/credit-proposal.docx")
t14 = doc.tables[14]
t15 = doc.tables[15]

# Table[14] r15 c1: Mối quan hệ — kỳ vọng "Cổ đông chính"
r15_val = t14.rows[15].cells[1].text.strip()
print(f"  Table[14] r15 c1 (mối quan hệ): {r15_val!r}")
assert r15_val == "Cổ đông chính", \
    f"FAIL: mối quan hệ = {r15_val!r}, kỳ vọng 'Cổ đông chính'"

# Table[14] r16 c1: Họ và tên cổ đông lớn nhất
r16_val = t14.rows[16].cells[1].text.strip()
print(f"  Table[14] r16 c1 (họ và tên):   {r16_val!r}")
assert r16_val, "FAIL: họ và tên cổ đông trống (Table[14] r16 c1)"

# Table[15] r5 c1: Tỷ lệ góp vốn
r5_val = t15.rows[5].cells[1].text.strip()
print(f"  Table[15] r5  c1 (tỷ lệ):       {r5_val!r}")
if r5_val:
    assert "%" in r5_val, f"FAIL: tỷ lệ '{r5_val}' không có '%'"
else:
    print("  WARN: tỷ lệ góp vốn trống (shareholders[0].percentage có thể là None)")

print("PASS")
PYEOF
```

---

## Bước 6.6 — PHỤ LỤC 6: KQKD lịch sử (Table[29])

8 rows × 5 cols. Col 2 = Năm N-1, Col 3 = Năm N (mới nhất).

```bash
python << 'PYEOF'
from docx import Document

doc = Document("data/outputs/mst/credit-proposal.docx")
t29 = doc.tables[29]

print("=== Table[29] — KQKD lịch sử ===")

# Row 0: headers phải là năm thực tế (4 chữ số)
year_n1 = t29.rows[0].cells[2].text.strip()
year_n  = t29.rows[0].cells[3].text.strip()
print(f"  r0 headers: col2={year_n1!r}  col3={year_n!r}")
assert year_n.isdigit() and len(year_n) == 4, \
    f"FAIL: col3 header '{year_n}' không phải năm 4 chữ số"

labels = [
    "Doanh thu",
    "Tổng chi phí",
    "Lợi nhuận sau thuế",
    "Vốn lưu động",
    "Vốn tự có (equity)",
    "Nợ phải trả (proxy vay TCTD)",
]

for ri, label in enumerate(labels, start=1):
    v2 = t29.rows[ri].cells[2].text.strip()
    v3 = t29.rows[ri].cells[3].text.strip()
    # rows 1-3: bắt buộc (revenue/costs/profit đã fill trước)
    # rows 4-6: mới fill — required nếu có financial data
    required = ri <= 3
    status = "OK" if (v3 or not required) else "FAIL"
    print(f"  r{ri} {label:30s}: N-1={v2!r:20s}  N={v3!r}")
    if status == "FAIL":
        raise AssertionError(f"FAIL: {label} (r{ri} c3) trống")
    if v3 and not any(u in v3 for u in ["tỷ", "triệu"]):
        print(f"    WARN: '{v3}' — kỳ vọng có đơn vị 'tỷ'/'triệu'")

print("PASS")
PYEOF
```

---

## Bước 6.7 — PHỤ LỤC 6: KQKD chi tiết 12 tháng (Table[32])

9 rows × 4 cols. Col 2 = giá trị 12 tháng năm mới nhất.

```bash
python << 'PYEOF'
from docx import Document

doc = Document("data/outputs/mst/credit-proposal.docx")
t32 = doc.tables[32]

print("=== Table[32] — KQKD chi tiết ===")

# r0 c2: header năm
header = t32.rows[0].cells[2].text.strip()
print(f"  r0 c2 (header năm): {header!r}")
assert "Năm" in header or header.isdigit(), \
    f"FAIL: header '{header}' không chứa 'Năm'"

checks = [
    (1, "Doanh thu bán hàng",    True),
    (2, "Giá vốn hàng bán",      False),  # có thể trống nếu không extract được
    (3, "Lợi nhuận gộp",         False),
    (7, "Chi phí khác",          False),
    (8, "Lợi nhuận sau thuế",    True),
]

for ri, label, required in checks:
    val = t32.rows[ri].cells[2].text.strip()
    status = "OK" if val else ("FAIL" if required else "SKIP")
    print(f"  r{ri} {label:30s}: {val!r}  [{status}]")
    if status == "FAIL":
        raise AssertionError(f"FAIL: {label} (r{ri} c2) trống")

print("PASS")
PYEOF
```

---

## Bước 6.8 — Kiểm tra Hoạt động kinh doanh (Table[8])

```bash
python << 'PYEOF'
from docx import Document

doc = Document("data/outputs/mst/credit-proposal.docx")
t8  = doc.tables[8]

biz = t8.rows[1].cells[0].text.strip()
print(f"  Table[8] r1 c0 (lĩnh vực KD): {biz!r}")
assert biz, "FAIL: lĩnh vực kinh doanh chính trống (Table[8] r1 c0)"
print("PASS")
PYEOF
```

---

## Bước 6.9 — Chạy tất cả checks trong một script

Script tổng hợp: chạy hết rồi báo PASS/FAIL từng mục.

```bash
python << 'PYEOF'
from docx import Document

OUTPUT = "data/outputs/mst/credit-proposal.docx"
doc = Document(OUTPUT)

results = []

def chk(section, desc, value, required=True, exact=None):
    if exact is not None:
        ok = value == exact
        note = f"got {value!r}, want {exact!r}"
    else:
        ok = bool(value)
        note = repr(value[:60]) if value else "EMPTY"
    status = "PASS" if ok else ("FAIL" if required else "SKIP")
    results.append((status, section, desc, note))

t1  = doc.tables[1]
t2  = doc.tables[2]
t3  = doc.tables[3]
t8  = doc.tables[8]
t14 = doc.tables[14]
t15 = doc.tables[15]
t29 = doc.tables[29]
t32 = doc.tables[32]

# --- Table[1]: Thông tin KH ---
chk("T1", "customer_name (r2c1)",    t1.rows[2].cells[1].text.strip())
chk("T1", "tax_code in r3c0",        t1.rows[3].cells[0].text.strip())
chk("T1", "address (r4c1)",          t1.rows[4].cells[1].text.strip())
chk("T1", "phone (r6c1)",            t1.rows[6].cells[1].text.strip())
chk("T1", "main_business (r7c1)",    t1.rows[7].cells[1].text.strip())
chk("T1", "charter_capital (r9c1)",  t1.rows[9].cells[1].text.strip())
chk("T1", "vot_thuc_gop (r10c1)",    t1.rows[10].cells[1].text.strip(), required=False)

# --- Table[2]: Người đại diện ---
chk("T2", "legal_rep (r14c1)",       t2.rows[14].cells[1].text.strip())
chk("T2", "board rows injected",     str(len(t2.rows) > 17), required=False)

# --- Table[3]: Cổ đông ---
chk("T3", "shareholder[0] name",     t3.rows[10].cells[1].text.strip())
chk("T3", "shareholder[0] pct",      t3.rows[10].cells[5].text.strip(), required=False)

# --- Table[8]: Ngành nghề ---
chk("T8", "main_business (r1c0)",    t8.rows[1].cells[0].text.strip())

# --- Table[14]/[15]: PHỤ LỤC 1 ---
chk("PL1", "moi_quan_he (T14 r15c1)", t14.rows[15].cells[1].text.strip(),
    exact="Cổ đông chính")
chk("PL1", "ho_ten (T14 r16c1)",      t14.rows[16].cells[1].text.strip())
chk("PL1", "ty_le (T15 r5c1)",        t15.rows[5].cells[1].text.strip(), required=False)

# --- Table[29]: KQKD lịch sử ---
year_n = t29.rows[0].cells[3].text.strip()
chk("T29", "header year col3",        year_n)
chk("T29", "revenue N (r1c3)",        t29.rows[1].cells[3].text.strip())
chk("T29", "costs N (r2c3)",          t29.rows[2].cells[3].text.strip())
chk("T29", "net_profit N (r3c3)",     t29.rows[3].cells[3].text.strip())
chk("T29", "working_capital N (r4c3)",t29.rows[4].cells[3].text.strip(), required=False)
chk("T29", "equity N (r5c3)",         t29.rows[5].cells[3].text.strip(), required=False)
chk("T29", "liabilities N (r6c3)",    t29.rows[6].cells[3].text.strip(), required=False)

# --- Table[32]: KQKD chi tiết ---
chk("T32", "header year (r0c2)",      t32.rows[0].cells[2].text.strip())
chk("T32", "revenue (r1c2)",          t32.rows[1].cells[2].text.strip())
chk("T32", "net_profit (r8c2)",       t32.rows[8].cells[2].text.strip())

# --- Print results ---
print(f"\n{'Status':5}  {'Table':5}  {'Field'}")
print("-" * 70)
pass_n = fail_n = skip_n = 0
for status, section, desc, note in results:
    print(f"  {status:4}  {section:4}  {desc:35s}  {note}")
    if status == "PASS": pass_n += 1
    elif status == "FAIL": fail_n += 1
    else: skip_n += 1

print("-" * 70)
print(f"  PASS: {pass_n}  FAIL: {fail_n}  SKIP: {skip_n}")

if fail_n:
    raise SystemExit(f"\n{fail_n} check(s) FAILED")
print("\nAll required checks PASSED")
PYEOF
```

---

## Scorecard DOCX

| Mục | Field | Kỳ vọng | Kết quả | Pass? |
|-----|-------|---------|---------|-------|
| T1 | Tên Khách hàng | Non-empty | | |
| T1 | Mã số thuế | Chứa MST | | |
| T1 | Địa chỉ | Non-empty | | |
| T1 | Điện thoại | Non-empty | | |
| T1 | Ngành nghề KD | Non-empty | | |
| T1 | Vốn điều lệ | Non-empty | | |
| T1 | Vốn thực góp | Có "tỷ"/"triệu" | | |
| T2 | Người đại diện | Non-empty | | |
| T2 | HĐQT/BGĐ/BKS | rows > 17 | | |
| T3 | Cổ đông r10 | name + tỷ lệ% | | |
| T8 | Lĩnh vực KD | Non-empty | | |
| T14 r15 | Mối quan hệ | "Cổ đông chính" | | |
| T14 r16 | Họ tên cổ đông | Non-empty | | |
| T15 r5 | Tỷ lệ góp vốn | Chứa "%" | | |
| T29 r0 | Header năm | 4 chữ số | | |
| T29 r1–r3 | Revenue/Costs/Profit | Có "tỷ"/"triệu" | | |
| T29 r4–r6 | Working capital/Equity/Liabilities | Có đơn vị | | |
| T32 r1,r8 | Revenue, Net profit | Có "tỷ"/"triệu" | | |
