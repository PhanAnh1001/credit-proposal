# AI Agent Tạo Tờ Trình Tín Dụng

AI Agent tự động tạo tờ trình thẩm định tín dụng từ báo cáo tài chính doanh nghiệp.

## Tổng quan

Hệ thống sử dụng **LangGraph** để orchestrate một multi-agent pipeline gồm 3 subgraph chính chạy tuần tự và song song, kèm vòng lặp tự sửa lỗi:

1. **Subgraph 1 — Thông tin công ty**: Đọc file Markdown → LLM trích xuất structured data (few-shot prompting)
2. **Subgraph 2 — Phân tích ngành**: Web search (Tavily) + LLM tổng hợp đánh giá ngành (Chain-of-Thought)
3. **Subgraph 3 — Phân tích tài chính**: PDF extraction → tính chỉ số tài chính (Python thuần) → LLM phân tích (CoT)

Output: 3 file tại `data/outputs/<company>/`:
- `credit-proposal.docx` — Mẫu đề nghị tín dụng điền sẵn thông tin (Output 1)
- `credit-analyst-memo.docx` — Tờ trình phân tích nội bộ (Output 2+3)
- `credit-analyst-memo.md` — Toàn bộ nội dung dạng Markdown

## Cài đặt

### Yêu cầu

- Python 3.12 (quản lý qua [uv](https://docs.astral.sh/uv))
- Groq API key (miễn phí tại [console.groq.com](https://console.groq.com))
- Tavily API key (miễn phí tại [tavily.com](https://tavily.com)) — tùy chọn

### Cài đặt

```bash
# Tạo virtual environment với Python 3.12
uv venv --python 3.12
source .venv/bin/activate

# Cài đặt dependencies
uv pip install -r requirements.txt
```

### Cấu hình environment

Tạo file `.env` từ `.env.example`:

```bash
cp .env.example .env
# Điền API keys vào .env
```

Các biến bắt buộc:
```env
GROQ_API_KEY=your_groq_api_key_here
TAVILY_API_KEY=your_tavily_api_key_here   # tùy chọn (fallback: LLM knowledge)
LANGSMITH_API_KEY=your_langsmith_key      # tùy chọn, để trace
LANGCHAIN_TRACING_V2=false               # set true để bật LangSmith tracing
OCR_ONLINE_DISABLED=false                # set true để bỏ qua Vision LLM OCR
```

## Sử dụng

### Chạy cơ bản (công ty MST)

```bash
python -m src.main
```

### Tùy chọn đầy đủ

```bash
python -m src.main \
  --company mst \
  --company-name "Công ty Cổ phần Xây dựng MST" \
  --base-dir data/uploads \
  --output-dir data/outputs/mst
```

### Output

Sau khi chạy, output được lưu tại `data/outputs/mst/`:
- `credit-proposal.docx` — Mẫu đề nghị tín dụng (Output 1)
- `credit-analyst-memo.docx` — Tờ trình phân tích nội bộ (Output 2+3)
- `credit-analyst-memo.md` — Toàn bộ nội dung Markdown

## Cấu trúc dự án

```
credit-proposal/
├── src/
│   ├── config.py             # Centralized path constants + env-var overrides
│   ├── agents/
│   │   ├── graph.py          # LangGraph graph definition
│   │   ├── subgraph1.py      # Company info extraction node
│   │   ├── subgraph2.py      # Sector analysis node
│   │   ├── subgraph3.py      # Financial analysis node
│   │   └── assembler.py      # Report assembly + quality review nodes
│   ├── tools/
│   │   ├── company_info.py   # read_md_company_info tool
│   │   ├── pdf_extractor.py  # extract_pdf_financial_tables tool
│   │   ├── ratio_calculator.py # calculate_financial_ratios tool
│   │   └── web_search.py     # web_search_industry tool
│   ├── models/
│   │   ├── state.py          # AgentState TypedDict
│   │   ├── company.py        # CompanyInfo Pydantic model
│   │   └── financial.py      # FinancialData Pydantic models
│   ├── utils/
│   │   ├── llm.py            # LLM factory (Groq) + rate-limit retry
│   │   ├── logger.py         # Colored logger + @timed_node decorator
│   │   ├── ocr_cache.py      # OCR result cache (file-based)
│   │   ├── docx_template.py  # DOCX template renderer
│   │   └── docx_converter.py # Markdown → DOCX converter
│   └── main.py               # CLI entry point
├── data/                      # Runtime data (gitignored except uploads + templates)
│   ├── uploads/{company}/     # Input files: general-information/md + financial-statements/pdf
│   ├── outputs/{company}/     # AI agent outputs (gitignored)
│   ├── cache/ocr/             # OCR result cache (gitignored)
│   └── templates/             # Reference form templates (docx/md/pdf)
├── docs/
│   ├── design/                # Agent design document
│   └── testing/               # Step-by-step test guides (01–06)
└── requirements.txt
```

## Kiến trúc Agent

```
User Input (PDF + MD)
       │
extract_company_info          ← Subgraph 1
   /            \
analyze_sector  analyze_financial   ← Subgraph 2 & 3 chạy song song (parallel fan-out)
   \            /
  assemble_report              ← fan-in: chờ cả 2 hoàn thành
       │
  quality_review               ← LLM-as-Judge (score 0-10 theo 3 output)
   /          \
 END     analyze_financial     ← self-correction: retry section yếu nhất (tối đa 1 lần)
      or analyze_sector
```

**Parallel fan-out**: `analyze_sector` và `analyze_financial` độc lập nhau, chạy đồng thời.
State fields ghi bởi 2 node là disjoint (`section_2_sector` vs `section_3_financial`).
Các shared fields dùng `Annotated` reducer để tránh `InvalidUpdateError`:
- `errors`, `messages` — `Annotated[list, add]`: parallel branches append an toàn
- `current_step` — `Annotated[str, lambda a, b: b]`: last-write-wins, vì cả 2 node đều ghi field này trong cùng step

**Self-correction loop**: `quality_review_node` chấm điểm từng output (0-10).
Nếu điểm < 7, `route_after_review()` re-run node yếu nhất với `quality_feedback` được inject vào prompt.
Tối đa 1 lần retry (`retry_count` ≥ 2 → END).

### Tools

| Tool | Chức năng |
|------|-----------|
| `read_md_company_info` | Đọc MD → LLM trích xuất CompanyInfo (tên, MST, địa chỉ, HĐQT, cổ đông...) |
| `extract_pdf_financial_tables` | PDF → PyMuPDF text / Vision LLM OCR → LLM parse → FinancialStatement dict |
| `calculate_financial_ratios` | Pure Python tính ROE, ROA, D/E, Current Ratio, Profit Margin, Revenue Growth |
| `web_search_industry` | Tavily search → LLM tổng hợp đánh giá ngành (fallback: LLM knowledge) |

### Memory & State

Sử dụng `AgentState` TypedDict của LangGraph làm short-term memory trong 1 session:
- Input: `company_name`, `md_company_info_path`, `pdf_dir_path`, `output_dir`
- Intermediate: `company_info`, `sector_info`, `financial_data`
- Sections: `section_1_company`, `section_2_sector`, `section_3_financial`
- Output: `final_report_md`, `final_report_docx_path`, `final_report_memo_docx_path`
- Quality loop: `retry_count`, `quality_review_result`, `quality_feedback`
- Control: `errors` (Annotated `add`), `messages` (Annotated `add`), `current_step` (Annotated last-write-wins)

### PDF Extraction Pipeline

PDFs BCTC thường là file scan (image-based). Pipeline xử lý theo thứ tự ưu tiên:

0. **PDF type detection** — sample 5 trang đại diện để phân loại `"text"` / `"image"` / `"mixed"`:
   - PDF `"image"` → bỏ qua bước 1 và 2, đi thẳng vào Vision OCR (tiết kiệm thời gian)
   - PDF `"text"` hoặc `"mixed"` → thử tuần tự từ bước 1
1. **PyMuPDF text** — nhanh, dành cho PDF có text layer
2. **markitdown** — broad format support
3. **TOC-guided Vision LLM OCR** — dành cho PDF scan:
   - **Image preprocessing** trước mỗi trang: grayscale → auto-contrast → contrast ×2.0 → sharpness ×2.5 → UnsharpMask; cải thiện OCR cho ảnh mờ hoặc chất lượng thấp
   - Render ở zoom 2.0× (~144 DPI, tăng từ 1.5×) để giữ chi tiết số liệu nhỏ
   - Đọc mục lục (trang 2-3) để biết trang bắt đầu của CĐKT/KQKD/LCTT
   - OCR chỉ các trang liên quan (không OCR toàn bộ 100 trang)
   - Kết quả cache tại `data/cache/ocr/`
4. **pdfplumber** — fallback cuối

### LLM Models (Groq)

Mỗi node dùng model riêng biệt — không có model nào chia sẻ giữa 2 chức năng:

| Node / Tác vụ | Model | TPM | RPD | Lý do |
|---------------|-------|-----|-----|-------|
| Subgraph 1 — Company info extraction | `qwen/qwen3-32b` | 6K | 1K | Qwen bucket riêng; xử lý tốt JSON extraction; sequential → không tranh TPM |
| Subgraph 2 — Sector synthesis | `openai/gpt-oss-120b` | 8K | 1K | OpenAI bucket riêng với SG3; max_tokens=4096 (~6.4K total, fits 120b window) |
| Subgraph 3 — Financial parse + narrative | `llama-3.3-70b-versatile` | **12K** | 1K | TPM cao nhất → SG3 nặng nhất (~79K tokens/run); 128K context; Meta bucket riêng |
| PDF — TOC parsing | `llama-3.1-8b-instant` | 6K | 14.4K | Input nhỏ (≤1K chars), RPD cao — tiết kiệm quota 1K-RPD models |
| PDF — Vision OCR | `llama-4-scout-17b-16e` | 30K | 1K | **Duy nhất** dùng llama-4-scout; image input; OCR cache giảm RPD |
| Quality review (LLM-as-Judge) | `openai/gpt-oss-20b` | 8K | 1K | max_tokens=2048; input QR ~1.3K → 3.3K total, fits ~8K window; OpenAI vendor |

> **Nguyên tắc phân bổ**: Mỗi chức năng dùng đúng 1 model riêng biệt. TPM cao nhất (`llama-3.3-70b`, 12K) → tác vụ nặng nhất (SG3 ~79K tokens/run). SG2 và SG3 chạy song song, dùng bucket TPM riêng biệt (OpenAI vs Meta) để tránh 429.
>
> **RPD strategy**: `llama-3.1-8b-instant` (14.4K RPD) cho TOC để bảo toàn quota 1K-RPD models. Tất cả models 1K-RPD còn lại đủ cho demo (≤10 runs/day).
>
> **LLM-as-Judge**: `openai/gpt-oss-20b` — cùng vendor OpenAI với SG2 (`gpt-oss-120b`) nhưng khác model; độc lập hoàn toàn với SG1 (Qwen) và SG3 (Meta). max_tokens=2048 đủ cho scoring JSON đầy đủ.

### Prompting Techniques

- **Chain-of-Thought (CoT)**: Các node phân tích tài chính và ngành dùng prompt yêu cầu LLM reasoning theo 5 bước rõ ràng trước khi kết luận
- **Few-shot examples**: `company_info.py` cung cấp 1 ví dụ đầy đủ input→output JSON để LLM calibrate format
- **Quality feedback injection**: Nếu retry, `quality_feedback` (top-3 issues từ reviewer) được inject vào prompt của node được re-run

## Chỉ số tài chính được tính

| Chỉ số | Công thức |
|--------|-----------|
| Current Ratio | Tài sản ngắn hạn / Nợ ngắn hạn |
| Quick Ratio | (TSNH - Hàng tồn kho) / Nợ ngắn hạn |
| D/E Ratio | Tổng nợ / Vốn CSH |
| D/A Ratio | Tổng nợ / Tổng tài sản |
| ROE | Lợi nhuận sau thuế / Vốn CSH × 100% |
| ROA | Lợi nhuận sau thuế / Tổng tài sản × 100% |
| Net Profit Margin | Lợi nhuận sau thuế / Doanh thu thuần × 100% |
| Gross Profit Margin | Lợi nhuận gộp / Doanh thu thuần × 100% |
| Revenue Growth YoY | (Doanh thu năm N - Doanh thu năm N-1) / Doanh thu năm N-1 × 100% |

## Lưu ý

- **Bảo mật**: Không commit API keys vào git. Dùng file `.env` (đã có trong `.gitignore`)
- **Chi phí**: Groq free tier đủ cho demo (14,400 req/ngày). Tavily free tier 1,000 req/tháng
- **Hallucination**: Số liệu tài chính được trích xuất từ PDF gốc và tính bằng Python thuần. LLM chỉ được dùng số liệu từ context, không được bịa
- **PDF scan**: Nếu PDF là ảnh scan, cần Groq vision model có quyền truy cập

## Chạy tests

```bash
pytest tests/ -v
```

Xem hướng dẫn test chi tiết từng bước tại [`docs/testing/`](docs/testing/README.md).
