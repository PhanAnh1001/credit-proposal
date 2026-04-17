# Test 01 — Kiểm tra môi trường

Trước khi chạy bất cứ thứ gì, xác nhận môi trường đã sẵn sàng.

---

## Bước 1.1 — Kiểm tra uv

```bash
uv --version
```

**Kỳ vọng**: `uv 0.x.x` (bất kỳ version nào).
**Nếu fail**: Cài uv theo hướng dẫn tại [docs.astral.sh/uv](https://docs.astral.sh/uv):
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

---

## Bước 1.2 — Tạo virtual environment với Python 3.12

```bash
# Nếu .venv đã tồn tại, bỏ qua hoặc tạo lại hoàn toàn
uv venv --python 3.12 --clear

source .venv/bin/activate
```

**Kỳ vọng**: Tạo thư mục `.venv/` trong project root, dùng Python 3.12.

Xác nhận version:
```bash
python --version
```

**Kỳ vọng**: `Python 3.12.x`

---

## Bước 1.3 — Cài đặt dependencies

```bash
uv pip install -r requirements.txt
```

**Kỳ vọng**: Tất cả packages cài thành công, không có error.
**Nếu fail**: Kiểm tra kết nối mạng và version Python.

---

## Bước 1.4 — File .env tồn tại và có key

```bash
cat .env
```

**Kỳ vọng**: File có các dòng sau (giá trị không rỗng):

```
GROQ_API_KEY=gsk_...
TAVILY_API_KEY=tvly-...     # optional nhưng cần cho test web search
LANGSMITH_API_KEY=ls__...   # optional, cho LangSmith tracing
```

**Nếu thiếu**: Copy từ `.env.example` và điền key thật.

```bash
cp .env.example .env
# Mở .env và điền key
```

---

## Bước 1.5 — Import các thư viện chính

```bash
python -c '
import sys
print(f"Python: {sys.version}")

libs = [
    "langgraph", "langchain", "langchain_groq", "groq",
    "pydantic", "pdfplumber", "fitz", "PIL", "docx",
    "tavily", "dotenv", "markitdown",
]
for lib in libs:
    try:
        __import__(lib)
        print(f"  OK  {lib}")
    except ImportError as e:
        print(f"  FAIL  {lib}  ->  {e}")
'
```

**Kỳ vọng**: Tất cả `OK`.
**Nếu fail**: Chạy lại `uv pip install -r requirements.txt` rồi thử lại.

---

## Bước 1.6 — Kết nối Groq API

```bash
python -c '
import os
from dotenv import load_dotenv
load_dotenv(".env")

from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage

llm = ChatGroq(
    api_key=os.getenv("GROQ_API_KEY"),
    model="llama-3.1-8b-instant",
    temperature=0.0,
    max_tokens=50,
)
response = llm.invoke([HumanMessage(content="Reply OK only.")])
print(f"Groq response: {response.content!r}")
'
```

**Kỳ vọng**: Response trả về (nội dung không quan trọng, miễn không raise exception).
**Nếu fail**: Kiểm tra `GROQ_API_KEY` trong `.env`.

---

## Bước 1.7 — Kiểm tra file input tồn tại

```bash
python -c '
from pathlib import Path

files_to_check = [
    "data/uploads/mst/general-information/md/mst-information.md",
    "data/uploads/mst/financial-statements/pdf/2022",
    "data/uploads/mst/financial-statements/pdf/2023",
    "data/uploads/mst/financial-statements/pdf/2024",
]

for path in files_to_check:
    p = Path(path)
    status = "OK" if p.exists() else "MISS"
    print(f"  {status}  {path}")

for year in [2022, 2023, 2024]:
    pdf_dir = Path(f"data/uploads/mst/financial-statements/pdf/{year}")
    pdfs = list(pdf_dir.glob("*.pdf")) if pdf_dir.exists() else []
    print(f"  PDF {year}: {len(pdfs)} file(s)  {[p.name for p in pdfs]}")
'
```

**Kỳ vọng**: Tất cả `OK`, mỗi thư mục năm có đúng 1 file PDF.

---

## Bước 1.8 — Kiểm tra thư mục output

```bash
ls -la data/outputs/mst/ 2>/dev/null || echo "Output dir does not exist yet (OK)"
```

Output dir chưa tồn tại là bình thường — sẽ được tạo tự động khi chạy pipeline.

---

## Kết quả mong đợi của Test 01

Tất cả bước `1.1` đến `1.7` phải pass. `1.8` là informational.

**Thời gian dự kiến**: 2-3 phút.
