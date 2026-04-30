# AI Agent Tạo Tờ Trình Tín Dụng

AI Agent tự động tạo tờ trình thẩm định tín dụng từ báo cáo tài chính doanh nghiệp.

> English version: [README.en.md](README.en.md)

## Tổng quan

Hệ thống sử dụng **LangGraph** để orchestrate một multi-agent pipeline gồm 3 subgraph chính chạy song song, kèm vòng lặp tự sửa lỗi và cơ chế escalation lên cán bộ thẩm định:

The system uses **LangGraph** to orchestrate a multi-agent pipeline with 3 main subgraphs running sequentially and in parallel, with a self-correction loop:

1. **Subgraph 1 — Company Info**: Reads a Markdown file → LLM extracts structured data (few-shot prompting)
2. **Subgraph 2 — Sector Analysis**: Web search (Tavily) + LLM synthesizes industry assessment (Chain-of-Thought)
3. **Subgraph 3 — Financial Analysis**: PDF extraction → financial ratio calculation (pure Python) → LLM narrative (CoT)

Output: 3 files saved to `data/outputs/<company>/`:
- `credit-proposal.docx` — Credit application form pre-filled with company data (Output 1)
- `credit-analyst-memo.docx` — Internal credit analyst memo (Output 2+3)
- `credit-analyst-memo.md` — Full content in Markdown

## Setup

### Requirements

- Python 3.12 (managed via [uv](https://docs.astral.sh/uv))
- Groq API key (free at [console.groq.com](https://console.groq.com))
- Tavily API key (free at [tavily.com](https://tavily.com)) — optional

### Installation

```bash
# Create virtual environment with Python 3.12
uv venv --python 3.12
source .venv/bin/activate

# Install dependencies
uv pip install -r requirements.txt
```

### Environment Configuration

Create a `.env` file from `.env.example`:

```bash
cp .env.example .env
# Fill in your API keys
```

Required variables:
```env
GROQ_API_KEY=your_groq_api_key_here
TAVILY_API_KEY=your_tavily_api_key_here   # optional (fallback: LLM knowledge)
LANGSMITH_API_KEY=your_langsmith_key      # optional, for tracing
LANGCHAIN_TRACING_V2=false               # set true to enable LangSmith tracing
OCR_ONLINE_DISABLED=false                # set true to skip Vision LLM OCR
```

## Usage

### Chạy cơ bản (công ty MST, ngân hàng VPBank)

```bash
python -m src.main
```

### Full options

```bash
python -m src.main \
  --bank vpbank \
  --company mst \
  --company-name "Công ty Cổ phần Xây dựng MST" \
  --base-dir data/uploads \
  --output-dir data/outputs/vpbank/mst
```

**Các tham số:**
| Tham số | Mặc định | Mô tả |
|---------|----------|-------|
| `--bank` | `vpbank` | Mã ngân hàng. Hiện hỗ trợ: `vpbank` |
| `--company` | `mst` | Mã công ty |
| `--company-name` | `Công ty Cổ phần Xây dựng MST` | Tên đầy đủ |
| `--base-dir` | `data/uploads` | Thư mục chứa file đầu vào |
| `--output-dir` | `data/outputs/<bank>/<company>` | Thư mục output |

### Output

Sau khi chạy, output được lưu tại `data/outputs/vpbank/mst/`:
- `credit-proposal.docx` — Mẫu đề nghị tín dụng (Output 1)
- `credit-analyst-memo.docx` — Tờ trình phân tích nội bộ (Output 2+3)
- `credit-analyst-memo.md` — Toàn bộ nội dung Markdown

## Project Structure

```
credit-proposal/
├── src/
│   ├── config.py             # Centralized path constants + env-var overrides
│   ├── agents/
│   │   ├── graph.py          # LangGraph graph definition + human_escalation_node
│   │   ├── subgraph1.py      # Company info extraction node
│   │   ├── subgraph2.py      # Sector analysis node
│   │   ├── subgraph3.py      # Financial analysis node
│   │   └── assembler.py      # Report assembly + quality review nodes
│   ├── banks/
│   │   ├── __init__.py       # Bank registry: SUPPORTED_BANKS, validate_bank(), get_renderer()
│   │   └── vpbank/
│   │       └── renderer.py   # VPBank form renderer — fills giay-de-nghi-vay-von.docx
│   ├── tools/
│   │   ├── company_info.py   # read_md_company_info tool
│   │   ├── pdf_extractor.py  # extract_pdf_financial_tables tool
│   │   ├── ratio_calculator.py # calculate_financial_ratios tool
│   │   ├── web_search.py     # web_search_industry tool
│   │   └── multi_layer_verifier.py # 4-layer verification (syntax/domain/regulatory/reasonableness)
│   ├── models/
│   │   ├── state.py          # AgentState TypedDict
│   │   ├── company.py        # CompanyInfo Pydantic model
│   │   ├── financial.py      # FinancialData Pydantic models
│   │   └── verification.py   # ClaimVerification + VerificationSummary Pydantic models
│   ├── knowledge/
│   │   ├── loader.py         # YAML loader with in-process cache + sector normalization
│   │   └── rules/
│   │       ├── financial_thresholds.yaml  # Ratio benchmarks by industry
│   │       └── reasonableness_bounds.yaml # YoY outlier + balance sheet tolerance
│   ├── utils/
│   │   ├── llm.py            # LLM factory (Groq)
│   │   ├── audit.py          # Structured JSONL audit trail
│   │   ├── checkpoint.py     # Per-node JSON checkpoints
│   │   ├── circuit_breaker.py # Per-subgraph abort checks
│   │   ├── validation.py     # Cross-agent validation gates (pure Python)
│   │   ├── ocr_cache.py      # OCR result cache (file-based)
│   │   ├── docx_template.py  # DOCX template shared helpers
│   │   └── docx_converter.py # Markdown → DOCX converter
│   └── main.py               # CLI entry point
├── data/                      # Runtime data (gitignored except uploads + templates)
│   ├── uploads/{company}/     # Input files: general-information/md + financial-statements/pdf
│   ├── outputs/{bank}/{company}/ # AI agent outputs (gitignored)
│   ├── cache/ocr/             # OCR result cache (gitignored)
│   ├── checkpoints/           # Per-run node checkpoints (gitignored)
│   └── templates/{bank}/      # Bank-specific form templates (docx/md/pdf)
├── ete-evidence/              # End-to-end run artifacts (screenshots, output samples)
├── docs/
│   ├── design/                # Agent design document
│   └── testing/               # Step-by-step test guides (01–06)
└── requirements.txt
```

## Agent Architecture

```
User Input (PDF + MD)
       │
extract_company_info          ← Subgraph 1 (circuit breaker + validation)
   /            \
analyze_sector  analyze_financial   ← Subgraph 2 & 3 chạy song song (parallel fan-out)
   \            /                     (circuit breaker + validation trong mỗi node)
  assemble_report              ← fan-in + multi-layer verifier (4 layers)
       │
  quality_review               ← LLM-as-Judge + per-claim confidence scoring
   /    |    \
 END  retry  human_escalation  ← tối đa 1 retry; escalate nếu score<7 sau retry
                                   hoặc low-confidence claims > 3
```

**Parallel fan-out**: `analyze_sector` and `analyze_financial` are independent and run concurrently.
State fields written by each node are disjoint (`section_2_sector` vs `section_3_financial`).
Shared fields use `Annotated` reducers to prevent `InvalidUpdateError`:
- `errors`, `messages` — `Annotated[list, add]`: parallel branches append safely
- `current_step` — `Annotated[str, lambda a, b: b]`: last-write-wins, since both nodes write this field in the same step

**Self-correction loop**: `quality_review_node` chấm điểm từng output (0-10).
Nếu điểm < 7, `route_after_review()` re-run node yếu nhất với `quality_feedback` được inject vào prompt.
Tối đa 1 lần retry.

**Human escalation**: Nếu score < 7 sau retry, hoặc phát hiện > 3 low-confidence claims từ verifier, pipeline escalate lên `human_escalation` node — tạo báo cáo markdown có cấu trúc để cán bộ thẩm định xử lý thủ công.

### Tools

| Tool | Description |
|------|-------------|
| `read_md_company_info` | Reads MD file → LLM extracts CompanyInfo (name, tax ID, address, board, shareholders…) |
| `extract_pdf_financial_tables` | PDF → PyMuPDF text / Vision LLM OCR → LLM parse → FinancialStatement dict |
| `calculate_financial_ratios` | Pure Python calculation of ROE, ROA, D/E, Current Ratio, Profit Margin, Revenue Growth |
| `web_search_industry` | Tavily search → LLM synthesizes industry assessment (fallback: LLM knowledge) |

### Memory & State

Sử dụng `AgentState` TypedDict của LangGraph làm short-term memory trong 1 session:
- Run identity: `run_id` (UUID4) — nhóm tất cả audit events và checkpoints
- Input: `bank`, `company_name`, `md_company_info_path`, `pdf_dir_path`, `output_dir`
- Intermediate: `company_info`, `sector_info`, `financial_data`
- Sections: `section_1_company`, `section_2_sector`, `section_3_financial`
- Output: `final_report_md`, `final_report_docx_path`, `final_report_memo_docx_path`
- Quality loop: `retry_count`, `quality_review_result`, `quality_feedback`
- Verification: `claim_verifications` (per-claim confidence), `verification_summary` (aggregate)
- Escalation: `escalation_report` (markdown report khi AI escalate lên human)
- Control: `errors` (Annotated `add`), `messages` (Annotated `add`), `current_step` (Annotated last-write-wins)

**Persistent state ngoài session**:
- OCR cache: `data/cache/ocr/{company}/{year}/` — tránh re-OCR PDF
- Node checkpoints: `data/checkpoints/{run_id}/` — JSON snapshot sau mỗi node quan trọng
- Audit trail: `logs/audit_YYYYMMDD.jsonl` — structured events theo `run_id`

### PDF Extraction Pipeline

Financial statement PDFs are often scanned image files. The pipeline processes them in priority order:

0. **PDF type detection** — samples 5 representative pages to classify as `"text"` / `"image"` / `"mixed"`:
   - `"image"` PDFs → skip steps 1–2, go directly to Vision OCR (saves time)
   - `"text"` or `"mixed"` → try sequentially from step 1
1. **PyMuPDF text** — fast, for PDFs with a text layer
2. **markitdown** — broad format support
3. **TOC-guided Vision LLM OCR** — for scanned PDFs:
   - **Image preprocessing** before each page: grayscale → auto-contrast → contrast ×2.0 → sharpness ×2.5 → UnsharpMask; improves OCR on blurry or low-quality scans
   - Renders at zoom 2.0× (~144 DPI, up from 1.5×) to preserve detail in small figures
   - Reads table of contents (pages 2–3) to locate balance sheet / income statement / cash flow start pages
   - OCRs only relevant pages (not the full 100-page document)
   - Results cached at `data/cache/ocr/`
4. **pdfplumber** — final fallback

### LLM Models (Groq)

Each node uses a dedicated model — no model is shared across two functions:

| Node / Task | Model | TPM | RPD | Reason |
|-------------|-------|-----|-----|--------|
| Subgraph 1 — Company info extraction | `qwen/qwen3-32b` | 6K | 1K | Separate Qwen bucket; handles JSON extraction well; sequential → no TPM contention |
| Subgraph 2 — Sector synthesis | `openai/gpt-oss-120b` | 8K | 1K | Separate OpenAI bucket from SG3; max_tokens=4096 (~6.4K total, fits 120b window) |
| Subgraph 3 — Financial parse + narrative | `llama-3.3-70b-versatile` | **12K** | 1K | Highest TPM → heaviest task (~79K tokens/run); 128K context; separate Meta bucket |
| PDF — TOC parsing | `llama-3.1-8b-instant` | 6K | 14.4K | Small input (≤1K chars), high RPD — preserves quota of 1K-RPD models |
| PDF — Vision OCR | `llama-4-scout-17b-16e` | 30K | 1K | Only model used for image input; OCR cache reduces RPD usage |
| Quality review (LLM-as-Judge) | `openai/gpt-oss-20b` | 8K | 1K | max_tokens=2048; QR input ~1.3K → 3.3K total, fits ~8K window; OpenAI vendor |

> **Allocation principle**: Each function uses exactly one dedicated model. Highest TPM (`llama-3.3-70b`, 12K) → heaviest task (SG3 ~79K tokens/run). SG2 and SG3 run in parallel using separate TPM buckets (OpenAI vs Meta) to avoid 429 errors.
>
> **RPD strategy**: `llama-3.1-8b-instant` (14.4K RPD) handles TOC parsing to preserve quota of 1K-RPD models. All remaining 1K-RPD models are sufficient for demo usage (≤10 runs/day).
>
> **LLM-as-Judge**: `openai/gpt-oss-20b` — same OpenAI vendor as SG2 (`gpt-oss-120b`) but different model size; fully independent from SG1 (Qwen) and SG3 (Meta). max_tokens=2048 sufficient for complete scoring JSON.

### Prompting Techniques

- **Chain-of-Thought (CoT)**: Financial and sector analysis nodes use prompts that require LLM reasoning through 5 explicit steps before drawing conclusions
- **Few-shot examples**: `company_info.py` provides one complete input→output JSON example for LLM format calibration
- **Quality feedback injection**: On retry, `quality_feedback` (top-3 issues from the reviewer) is injected into the re-run node's prompt

## Financial Ratios Calculated

| Ratio | Formula |
|-------|---------|
| Current Ratio | Current Assets / Current Liabilities |
| Quick Ratio | (Current Assets − Inventory) / Current Liabilities |
| D/E Ratio | Total Debt / Equity |
| D/A Ratio | Total Debt / Total Assets |
| ROE | Net Income / Equity × 100% |
| ROA | Net Income / Total Assets × 100% |
| Net Profit Margin | Net Income / Net Revenue × 100% |
| Gross Profit Margin | Gross Profit / Net Revenue × 100% |
| Revenue Growth YoY | (Revenue Year N − Revenue Year N-1) / Revenue Year N-1 × 100% |

## Guardrails và chất lượng

| Lớp | Cơ chế | Khi nào |
|-----|--------|---------|
| **Circuit breaker** | Kiểm tra `total_assets=0`, sector text < 200 chars, company_name rỗng | Sau mỗi subgraph |
| **Validation gate** | Pure Python: tax_code regex, shareholders%, established_date | Sau extract/analyze |
| **Domain knowledge** | YAML thresholds (current_ratio, D/E, ROE theo ngành) | Trong multi-layer verifier |
| **Multi-layer verifier** | 4 layers: syntax → domain → regulatory → reasonableness | Trong assemble_report |
| **LLM-as-Judge** | `gpt-oss-20b` (OpenAI) đánh giá output SG1(Qwen) + SG3(Meta) | Sau assemble_report |
| **Per-claim confidence** | `ClaimVerification` model: confidence 0.0–1.0 per claim | Trong quality_review |
| **Human escalation** | Báo cáo markdown chi tiết khi AI không đủ confidence | Khi score<7 after retry hoặc low-conf claims>3 |

## Lưu ý

- **Bảo mật**: Không commit API keys vào git. Dùng file `.env` (đã có trong `.gitignore`)
- **Chi phí**: Groq free tier đủ cho demo (14,400 req/ngày). Tavily free tier 1,000 req/tháng
- **Hallucination**: Số liệu tài chính được trích xuất từ PDF gốc và tính bằng Python thuần. LLM chỉ được dùng số liệu từ context, không được bịa
- **PDF scan**: Nếu PDF là ảnh scan, cần Groq vision model có quyền truy cập
- **Audit trail**: Tất cả events được ghi vào `logs/audit_YYYYMMDD.jsonl` theo `run_id` để debug và review

## Running Tests

```bash
pytest tests/ -v
```

See step-by-step test guides at [`docs/testing/`](docs/testing/README.md).
