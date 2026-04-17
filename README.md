# AI Credit Proposal Agent

Automated AI agent that generates credit appraisal memos from corporate financial statements.

> Vietnamese version: [README.vi.md](README.vi.md)

## Overview

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

### Basic run (MST company)

```bash
python -m src.main
```

### Full options

```bash
python -m src.main \
  --company mst \
  --company-name "Cong ty Co phan Xay dung MST" \
  --base-dir data/uploads \
  --output-dir data/outputs/mst
```

### Output

After running, outputs are saved to `data/outputs/mst/`:
- `credit-proposal.docx` — Credit application form (Output 1)
- `credit-analyst-memo.docx` — Internal credit analyst memo (Output 2+3)
- `credit-analyst-memo.md` — Full Markdown content

## Project Structure

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

## Agent Architecture

```
User Input (PDF + MD)
       │
extract_company_info          ← Subgraph 1
   /            \
analyze_sector  analyze_financial   ← Subgraph 2 & 3 run in parallel (fan-out)
   \            /
  assemble_report              ← fan-in: waits for both to complete
       │
  quality_review               ← LLM-as-Judge (scores 0–10 per output)
   /          \
 END     analyze_financial     ← self-correction: retry weakest section (max 1 time)
      or analyze_sector
```

**Parallel fan-out**: `analyze_sector` and `analyze_financial` are independent and run concurrently.
State fields written by each node are disjoint (`section_2_sector` vs `section_3_financial`).
Shared fields use `Annotated` reducers to prevent `InvalidUpdateError`:
- `errors`, `messages` — `Annotated[list, add]`: parallel branches append safely
- `current_step` — `Annotated[str, lambda a, b: b]`: last-write-wins, since both nodes write this field in the same step

**Self-correction loop**: `quality_review_node` scores each output section (0–10).
If any score < 7, `route_after_review()` re-runs the weakest node with `quality_feedback` injected into the prompt.
Maximum 1 retry (`retry_count` ≥ 2 → END).

### Tools

| Tool | Description |
|------|-------------|
| `read_md_company_info` | Reads MD file → LLM extracts CompanyInfo (name, tax ID, address, board, shareholders…) |
| `extract_pdf_financial_tables` | PDF → PyMuPDF text / Vision LLM OCR → LLM parse → FinancialStatement dict |
| `calculate_financial_ratios` | Pure Python calculation of ROE, ROA, D/E, Current Ratio, Profit Margin, Revenue Growth |
| `web_search_industry` | Tavily search → LLM synthesizes industry assessment (fallback: LLM knowledge) |

### Memory & State

Uses LangGraph's `AgentState` TypedDict as short-term memory within a single session:
- Input: `company_name`, `md_company_info_path`, `pdf_dir_path`, `output_dir`
- Intermediate: `company_info`, `sector_info`, `financial_data`
- Sections: `section_1_company`, `section_2_sector`, `section_3_financial`
- Output: `final_report_md`, `final_report_docx_path`, `final_report_memo_docx_path`
- Quality loop: `retry_count`, `quality_review_result`, `quality_feedback`
- Control: `errors` (Annotated `add`), `messages` (Annotated `add`), `current_step` (Annotated last-write-wins)

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

## Notes

- **Security**: Do not commit API keys to git. Use the `.env` file (already in `.gitignore`)
- **Cost**: Groq free tier is sufficient for demos (14,400 req/day). Tavily free tier: 1,000 req/month
- **Hallucination prevention**: Financial figures are extracted from the original PDF and calculated with pure Python. The LLM is only permitted to use numbers from the provided context
- **Scanned PDFs**: If the PDF is image-based, a Groq vision model with access is required

## Running Tests

```bash
pytest tests/ -v
```

See step-by-step test guides at [`docs/testing/`](docs/testing/README.md).
