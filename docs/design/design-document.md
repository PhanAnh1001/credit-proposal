# Design Document — AI Agent Tạo Tờ Trình Tín Dụng

**Version**: 2.0 | **Date**: 28/04/2026

---

## 1. Bài toán và mục tiêu

Tự động hoá quy trình soạn tờ trình thẩm định tín dụng từ báo cáo tài chính (BCTC) doanh nghiệp. Input: file PDF BCTC 3 năm + file Markdown thông tin công ty. Output: 3 phần chuẩn hoá dưới dạng DOCX + Markdown.

**3 output yêu cầu:**
1. Thông tin chung: tên, MST, địa chỉ, HĐQT, cổ đông lớn
2. Phân tích lĩnh vực kinh doanh: tổng quan ngành, triển vọng, rủi ro
3. Phân tích tài chính: số liệu CĐKT/KQKD, chỉ số tài chính, nhận xét so sánh

---

## 2. Kiến trúc tổng quan

Framework: **LangGraph** (StateGraph) — orchestrate multi-node pipeline với parallel execution và conditional routing.

```
extract_company_info (Subgraph 1)
        /                 \
analyze_sector      analyze_financial     ← chạy song song
(Subgraph 2)        (Subgraph 3)
        \                 /
      assemble_report                     ← fan-in + multi-layer verifier
             |
       quality_review                     ← LLM-as-Judge + claim verification
        /    |    \
      END  retry  human_escalation        ← tối đa 1 retry; escalate nếu score<7 sau retry
                                             hoặc low-confidence claims > 3
```

**Parallel fan-out**: Subgraph 2 và 3 độc lập (ghi vào state fields khác nhau), LangGraph chạy đồng thời, giảm latency ~40%.

**Self-correction loop**: Quality review node chấm điểm 3 output (0–10). Nếu điểm < 7, router route về node yếu nhất với `quality_feedback` chứa top-3 vấn đề cần sửa. Tối đa 1 lần retry.

**Human escalation**: Nếu score < 7 sau 1 retry (retry_count ≥ 2), hoặc phát hiện > 3 low-confidence claims, pipeline escalate lên `human_escalation` node thay vì silent fail. Node này tạo báo cáo markdown liệt kê vấn đề cụ thể để cán bộ thẩm định xử lý thủ công.

---

## 3. Memory

| Loại | Cơ chế | Mục đích |
|------|--------|----------|
| **Short-term (trong session)** | `AgentState` TypedDict (LangGraph) | Lưu trữ và truyền dữ liệu giữa các node trong 1 pipeline run |
| **OCR cache (persistent)** | File-based JSON (`data/cache/ocr/`) | Tránh re-OCR PDF khi chạy lại; cache theo company/year/strategy |
| **Node checkpoints** | JSON files (`data/checkpoints/{run_id}/`) | Snapshot trạng thái sau mỗi node quan trọng để debug và audit |

`AgentState` fields chính:
- `run_id: str` — UUID4 cho mỗi pipeline invocation; nhóm tất cả audit events, checkpoints
- `company_info`, `financial_data`, `sector_info` — intermediate data
- `section_1/2/3_*` — output từng section
- `quality_review_result`, `quality_feedback`, `retry_count` — quality loop
- `claim_verifications: list[dict]` — per-claim verification kết quả (từ `ClaimVerification` model)
- `verification_summary: dict` — aggregated summary từ multi-layer verifier
- `escalation_report: str | None` — markdown report khi pipeline escalate lên human
- `errors`, `messages` — `Annotated[list, add]`: parallel branches append, không overwrite nhau
- `current_step` — `Annotated[str, lambda a, b: b]` (last-write-wins): cả hai parallel node (`analyze_sector`, `analyze_financial`) đều ghi field này trong cùng step; LangGraph `LastValue` mặc định sẽ throw `InvalidUpdateError` nếu không có reducer

---

## 4. Planning

Pattern: **Plan-and-Execute với fixed plan**. Graph topology cố định (không dynamic); mỗi node có nhiệm vụ xác định. Không dùng ReAct vì bài toán có cấu trúc rõ ràng, không cần dynamic tool selection.

**Routing logic** (`route_after_review`):
```
low_confidence_count > 3 → human_escalation   (per-claim confidence từ multi-layer verifier)
score ≥ 7 → END
retry_count ≥ 2 và score < 7 → human_escalation
financial_quality ≤ sector_quality và < 7 → retry analyze_financial
sector_quality < 7 → retry analyze_sector
else → END
```

---

## 5. Tools

| Tool | File | Input | Output |
|------|------|-------|--------|
| `read_md_company_info` | `tools/company_info.py` | Path file MD | `CompanyInfo` dict (JSON) |
| `extract_pdf_financial_tables` | `tools/pdf_extractor.py` | PDF dir path, company, year | `FinancialData` dict |
| `calculate_financial_ratios` | `tools/ratio_calculator.py` | FinancialData dict | Ratios dict (Python thuần, không LLM) |
| `web_search_industry` | `tools/web_search.py` | Tên ngành, company, extra_hint | Sector analysis text |

**PDF extraction — 5 bước (ưu tiên giảm dần):**
0. **PDF type detection** — sample 5 trang đại diện, đếm printable chars; phân loại "text" / "image" / "mixed" để skip chiến lược không phù hợp và tiết kiệm thời gian
1. PyMuPDF text layer (text-based PDFs) — bỏ qua nếu type = "image"
2. markitdown conversion — bỏ qua nếu type = "image"
3. **Image preprocessing** — trước khi gửi mỗi trang ảnh cho Vision LLM: grayscale → auto-contrast → contrast boost (×2.0) → sharpness boost (×2.5) → UnsharpMask; cải thiện OCR cho ảnh mờ, chất lượng thấp
4. **TOC-guided Vision LLM OCR** — đọc mục lục → target trang CĐKT/KQKD → Vision OCR chỉ các trang đó (zoom 2.0× thay vì 1.5×, cộng với image preprocessing)
5. pdfplumber (fallback)

**Financial ratio calculator** — pure Python, deterministic, không dùng LLM:
Current Ratio, Quick Ratio, D/E, D/A, ROE, ROA, Net Profit Margin, Gross Profit Margin, Revenue Growth YoY

---

## 6. Actions (Nodes)

| Node | Prompting technique | Mô tả |
|------|---------------------|-------|
| `extract_company_info` | Few-shot (1 ví dụ đầy đủ) | Đọc MD → LLM extract → Pydantic CompanyInfo → circuit breaker + validation |
| `analyze_sector` | Chain-of-Thought (5 bước) | Web search + LLM synthesis → section 2 → circuit breaker + validation |
| `analyze_financial` | Chain-of-Thought (5 bước) + 2-stage LLM parse | PDF extract → ratios → LLM analyze → section 3 → circuit breaker + validation |
| `assemble_report` | Template-based | Ghép 3 sections → MD + 2 DOCX → multi-layer verifier (4 layers) |
| `quality_review` | LLM-as-Judge | Score 3 outputs (0-10) + claim-level confidence + quality_feedback |
| `human_escalation` | Template-based | Format báo cáo markdown cho cán bộ thẩm định khi AI không đủ confidence |

**2-stage financial parsing** (trong `analyze_financial`):
- Stage 1: Extract CĐKT (≤11k chars context) với CoT prompt 5 bước
- Stage 2: Extract KQKD (≤5k chars context) với CoT prompt 5 bước
- Chia nhỏ để tránh vượt TPM limit Groq free tier

---

## 7. LLM Models (Groq API)

| Node / Tác vụ | Model | Vendor | TPM | Lý do |
|---------------|-------|--------|-----|-------|
| Subgraph 1 — Company info extraction | `qwen/qwen3-32b` | Qwen | 6K | Qwen bucket riêng; xử lý tốt JSON extraction; sequential → không tranh TPM với SG2/3 |
| Subgraph 2 — Sector synthesis | `openai/gpt-oss-120b` | OpenAI | 8K | OpenAI bucket riêng với SG3 (Meta); max_tokens=4096 → ~6.4K total, fits 120b window |
| Subgraph 3 — Financial parse (Stage 1+2) | `llama-3.3-70b-versatile` | Meta | **12K** | TPM cao nhất → SG3 nặng nhất (~79K tokens/run); 128K context đủ cho toàn bộ BCTC |
| Subgraph 3 — Financial narrative | `llama-3.3-70b-versatile` | Meta | **12K** | Cùng model với parse stages — context đủ, không thêm RPD usage |
| PDF — TOC parsing | `llama-3.1-8b-instant` | Meta | 6K | Input ≤1K chars, tiết kiệm quota; 14.4K RPD phù hợp cho nhiều lần chạy |
| PDF — Vision OCR | `llama-4-scout-17b-16e` | Meta | 30K | Model duy nhất hỗ trợ image input trên Groq; kết quả OCR được cache |
| Quality review (LLM-as-Judge) | `openai/gpt-oss-20b` | OpenAI | 8K | max_tokens=2048; input QR ~1.3K → 3.3K total, fits ~8K window; độc lập với SG1 (Qwen) và SG3 (Meta) |

**Chiến lược TPM isolation**: Subgraph 2 và 3 chạy song song (parallel fan-out). Hai subgraph dùng bucket TPM riêng biệt — SG2 dùng OpenAI bucket (`gpt-oss-120b`, 8K TPM), SG3 dùng Meta bucket (`llama-3.3-70b`, 12K TPM) — mỗi subgraph có pool TPM độc lập, tránh tranh quota và lỗi 429. Model có TPM cao nhất được gán cho tác vụ nặng nhất (SG3 ~79K tokens/run gồm 3 năm × 3 stages).

**LLM-as-Judge independence**: Judge (`openai/gpt-oss-20b`, OpenAI) độc lập với SG1 (`qwen/qwen3-32b`, Qwen) và SG3 (`llama-3.3-70b`, Meta) — tránh vendor self-confirmation bias khi đánh giá output. Cùng vendor OpenAI với SG2 (`gpt-oss-120b`) nhưng khác model size — đây là best achievable trên Groq free tier.

**Rate-limit handling**: `invoke_with_retry()` (`utils/llm.py`) tự parse thời gian chờ từ error message Groq ("try again in Xs"), sleep đúng khoảng đó (+2s buffer), retry tối đa 2 lần.

---

## 8. Guardrails và chất lượng

- **Factual grounding**: Prompt yêu cầu LLM chỉ dùng số liệu từ context, cấm bịa số
- **Deterministic ratios**: Financial ratios tính bằng Python, không qua LLM
- **Unit normalization**: Auto-detect raw VND vs triệu đồng (so sánh `total_assets` với threshold)
- **Cross-validation**: Kiểm tra accounting identity (total_assets ≈ total_liabilities + equity)
- **Structured output**: JSON schema enforce từ LLM → parse với `json.loads()` + fallback
- **Rate-limit retry**: `invoke_with_retry()` parse "try again in Xs" từ error Groq → sleep đúng thời gian đó (+2s buffer), retry 2 lần; dùng cho tất cả LLM calls trong web_search, pdf_extractor, assembler
- **PDF type detection**: `_detect_pdf_type()` phân loại PDF trước khi xử lý — image-based PDF bỏ qua chiến lược text extraction, tiết kiệm thời gian và tránh gọi API không cần thiết
- **Image preprocessing**: `_preprocess_page_image()` áp dụng trước mỗi Vision OCR call — grayscale + auto-contrast + contrast/sharpness boost + UnsharpMask cải thiện độ chính xác OCR cho ảnh scan chất lượng thấp hoặc bị mờ; zoom mặc định tăng từ 1.5× lên 2.0× (~144 DPI)
- **Circuit breaker** (`src/utils/circuit_breaker.py`): kiểm tra điều kiện abort sau mỗi subgraph — `total_assets == 0` tất cả năm, `section_2_sector` < 200 chars, `company_name` rỗng. Khi trip: ghi warning vào `state['errors']` và audit log, pipeline tiếp tục nhưng escalation được trigger downstream. Ngưỡng load từ YAML (không hardcode).
- **Cross-agent validation gate** (`src/utils/validation.py`): pure Python, không tốn LLM call — kiểm tra 5 quy tắc company info (tên, ngành nghề, mã số thuế regex 10/13 số), sector output (length, số rủi ro), financial output (required fields, ratio sanity). Kết quả được log vào audit trail.
- **Domain knowledge base** (`src/knowledge/rules/`): 2 YAML files — `financial_thresholds.yaml` (ngưỡng current_ratio, D/E, ROE theo ngành); `reasonableness_bounds.yaml` (YoY outlier thresholds, balance sheet tolerance). Hot-reloadable, in-process cache. Sector normalization map tiếng Việt → YAML key.
- **Multi-layer verifier** (`src/tools/multi_layer_verifier.py`): 4 layers chạy trong `assemble_report`:
  - Layer 1 — Syntax: required fields có giá trị?
  - Layer 2 — Domain: ratio vs industry benchmarks (từ YAML)?
  - Layer 3 — Regulatory: stub NHNN Circular 11/2021 (placeholder, cần legal team hoàn thiện)
  - Layer 4 — Reasonableness: YoY outliers, balance sheet internal consistency
- **Per-claim confidence scoring** (`src/models/verification.py`): `ClaimVerification` Pydantic model — mỗi claim có `confidence` (0.0–1.0), `verified`, `issues`, `regulation_refs`. `VerificationSummary` aggregate: `low_confidence_count`, `needs_escalation()`. Routing dùng `low_confidence_count > 3` để trigger escalation độc lập với overall score.
- **Human escalation node**: Khi AI không đủ confidence, tạo báo cáo markdown có structure rõ ràng: điểm chất lượng, số lần retry, danh sách low-confidence claims với confidence score, vấn đề AI không giải quyết được, hành động cần thiết cho cán bộ thẩm định.

---

## 9. Observability

- **LangSmith tracing**: Bật qua `LANGCHAIN_TRACING_V2=true` — trace từng node, LLM call, token usage
- **Structured logging**: `timed_node` decorator log elapsed time cho mỗi node
- **OCR cache**: Mỗi OCR run lưu metadata (elapsed, pages, strategy) tại `data/cache/ocr/`
- **Structured audit trail** (`src/utils/audit.py`): JSONL append-only tại `logs/audit_YYYYMMDD.jsonl`. Events: `pipeline_start/end`, `node_start/end` (với elapsed_s), `circuit_breaker_trip/warn`, `llm_call` (model, prompt_chars, think-block preserved), `quality_decision` (score, route, retry_triggered), `validation_result` (passed, failures). Think-blocks NOT stripped — giữ nguyên để audit CoT reasoning. Được nhóm theo `run_id` (UUID4) cho mỗi pipeline invocation.
- **Lightweight checkpoints** (`src/utils/checkpoint.py`): JSON files tại `data/checkpoints/{run_id}/`. Sau mỗi subgraph quan trọng: `01_company_info.json`, `02_financial_data.json`, `escalation.json`. `save_run_meta()` lưu company + timestamp + status. Dùng để debug và recover state mà không cần re-run toàn pipeline.

## 10. File mới (so với v1.0)

| File | Vai trò |
|------|---------|
| `src/knowledge/rules/financial_thresholds.yaml` | Ngưỡng ratio theo ngành (current_ratio, D/E, ROE, ROA...) |
| `src/knowledge/rules/reasonableness_bounds.yaml` | Bounds YoY outlier, balance sheet tolerance, required fields |
| `src/knowledge/loader.py` | Load + deep-merge YAML rules, sector normalization |
| `src/utils/circuit_breaker.py` | Kiểm tra điều kiện abort sau mỗi subgraph |
| `src/utils/audit.py` | JSONL audit trail, structured events theo run_id |
| `src/utils/checkpoint.py` | Per-node JSON checkpoints |
| `src/utils/validation.py` | Cross-agent validation gates (pure Python) |
| `src/models/verification.py` | `ClaimVerification` + `VerificationSummary` Pydantic models |
| `src/tools/multi_layer_verifier.py` | 4-layer verifier (syntax, domain, regulatory stub, reasonableness) |
