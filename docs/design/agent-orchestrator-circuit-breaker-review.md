# Review: Agent Orchestrator với Circuit Breaker

**Ngày review:** 2026-04-20
**Branch:** `claude/domain-verification-agent-WDmUD`
**Scope:** Đánh giá dự án Credit Proposal AI Agent theo yêu cầu Agent Orchestrator với Circuit Breaker

---

## Tổng quan hiện trạng

Dự án là một LangGraph pipeline 5-node với parallel fan-out/fan-in và self-correction loop. Một số orchestration patterns đã có, nhưng circuit breaker, checkpoint/rollback, cross-agent validation, và audit trail đều thiếu hoặc chỉ có dạng primitive.

**Pipeline hiện tại:**
```
extract_company_info (entry)
    ↓
[analyze_sector] ‖ [analyze_financial]   ← parallel fan-out
    ↓
assemble_report                          ← fan-in
    ↓
quality_review (LLM-as-Judge)
    ↓ conditional routing (score < 7 → retry, max 1 lần)
END
```

---

## Gap Analysis theo từng yêu cầu

### 1. Circuit Breaker Pattern

**Yêu cầu:** Tự ngắt workflow khi output bất thường (confidence thấp, anomaly detection). Graceful degradation thay vì cascading failure.

**Hiện tại:**

| Cơ chế | Trạng thái |
|---|---|
| Rate-limit retry (`invoke_with_retry`) | ✅ Có — parse "try again in Xs", retry 2 lần |
| Error accumulation (`state['errors']`) | ✅ Có — append errors, không crash ngay |
| PDF fallback strategies (4 strategies) | ✅ Có — graceful degradation theo tầng |
| Quality score threshold (< 7 → retry) | ⚠️ Partial — chỉ trigger retry, không ngắt circuit |
| Anomaly detection trên output | ❌ Không có |
| Confidence threshold để abort workflow | ❌ Không có |
| Cascading failure prevention | ❌ Không có — nếu SG3 trả rỗng, assembler vẫn chạy |

**Gap cụ thể:**
```python
# Hiện tại: node luôn tiếp tục dù output rỗng/bất thường
def analyze_financial_node(state):
    try:
        result = extract_pdf_financial_tables(...)
    except Exception as e:
        state['errors'].append(str(e))
    return state  # Pipeline vẫn tiếp tục với data thiếu

# Cần: circuit breaker ngắt sớm khi phát hiện bất thường
def analyze_financial_node(state):
    result = extract_pdf_financial_tables(...)
    if _is_anomalous(result):           # YoY revenue +500%? total_assets = 0?
        raise CircuitBreakerTripped(
            reason="financial_data_anomaly",
            fallback=_get_cached_result(state)
        )
```

**Anomaly chưa được detect:**
- `total_assets = 0` hoặc `None` sau 3 PDF (silent failure)
- Revenue YoY > 500% (có thể hallucination)
- Balance sheet equation fail > tolerance nhưng pipeline vẫn tiếp tục
- Sector output < 200 chars (synthesis thất bại âm thầm)

---

### 2. State Checkpoint & Rollback

**Yêu cầu:** Mỗi bước agent tạo snapshot. Lưu: input, agent decision, tool call, output, intermediate states. Cho phép rollback một phần.

**Hiện tại:**

| Cơ chế | Trạng thái |
|---|---|
| LangGraph StateGraph in-memory state | ✅ Có — `AgentState` TypedDict persist qua nodes |
| OCR cache (`data/cache/ocr/`) | ✅ Có — versioned OCR results, reusable |
| `financial_data.json`, `ratios.json` | ✅ Có — intermediate output cached |
| Checkpoint trước mỗi node | ❌ Không có |
| Snapshot có timestamp + metadata | ❌ Không có (OCR cache có, nhưng chỉ cho OCR) |
| Rollback về checkpoint cụ thể | ❌ Không có |
| Persistent state giữa các run | ❌ Không có — mỗi run bắt đầu lại từ đầu |

**Gap cụ thể:**

OCR cache hiện tại (`data/cache/ocr/{company}/{year}/vision_llm/{YYYYMMDD}_vN/`) là dạng checkpoint đúng hướng, nhưng chỉ cover 1 trong 5 nodes (SG3 PDF extraction). Không có checkpoint cho:
- CompanyInfo extraction output (SG1)
- Sector synthesis output (SG2)
- Assembled report (assembler)
- Quality review scores (QR)

```
Cần thêm checkpoint layer:
data/checkpoints/{run_id}/
  00_initial_state.json
  01_company_info.json          # output SG1
  02a_sector_analysis.json      # output SG2
  02b_financial_data.json       # output SG3 (đã có trong ocr cache)
  03_assembled_report.json      # output assembler
  04_quality_review.json        # output QR
  meta.json                     # run_id, timestamp, status
```

Rollback use case quan trọng nhất: SG3 (financial) thất bại → rollback về `01_company_info.json`, retry chỉ SG3 mà không re-extract SG1 — tiết kiệm 1-2 LLM call + 1-2 phút OCR.

---

### 3. Cross-Agent Validation

**Yêu cầu:** Output agent A được agent kiểm tra độc lập trước khi trở thành input agent B. Validation rules specific to domain.

**Hiện tại:**

| Cơ chế | Trạng thái |
|---|---|
| Balance sheet cross-validation | ✅ Có — `total_assets ≈ liabilities + equity` (±2%) |
| Unit normalization (`_normalize_units`) | ✅ Có — phát hiện raw VND |
| Derived field computation | ✅ Có — accounting identities |
| LLM-as-Judge (QR node) | ⚠️ Partial — review cuối pipeline, không phải sau từng node |
| Validation SG1 output trước khi SG2/3 nhận | ❌ Không có |
| Validation SG2 output trước khi assembler nhận | ❌ Không có |
| Domain-specific validation rules | ❌ Không có (ngoài balance sheet) |

**Gap cụ thể:**

```
Luồng hiện tại:
SG1 output → [ngay] → SG2/SG3 input (không qua validation)
SG2/SG3 output → [ngay] → assembler input
assembler output → QR (review tổng thể)

Luồng cần thiết:
SG1 output → [cross-validate: company_name không rỗng? tax_code hợp lệ?]
           → SG2/SG3 input
SG2 output → [cross-validate: sector đúng industry của công ty? ≥ 4 risks?]
           → assembler input
SG3 output → [cross-validate: 3 năm đủ? ratios trong range hợp lý?]
           → assembler input
```

**Validation rules thiếu:**
- Tax code format (10 chữ số, check digit)
- Industry code consistency (SG2 analyze đúng ngành SG1 extract chưa?)
- Năm tài chính coverage (có đủ 2022, 2023, 2024 chưa?)
- Ratio sanity bounds (Current Ratio > 20 là suspicious, có thể data entry error)
- Cross-check: revenue trong BCTC vs. revenue trong company info description

---

### 4. Audit Trail Tự động

**Yêu cầu:** Ghi lại toàn bộ chain-of-thought, tool calls, quyết định, dữ liệu vào/ra. Queryable format, time-series tracking.

**Hiện tại:**

| Cơ chế | Trạng thái |
|---|---|
| Console logging với ANSI colors | ✅ Có — INFO/WARNING/ERROR |
| File logging `logs/run_YYYYMMDD.log` | ✅ Có — hàng ngày |
| `@timed_node` decorator | ✅ Có — log START/END + elapsed time |
| `state['messages']` timing info | ✅ Có — Annotated[list, add] |
| LangSmith tracing (optional) | ✅ Có — nếu có API key |
| Queryable audit trail | ❌ Không có — log là plaintext, không query được |
| Tool call recording | ❌ Không có — chỉ log node level, không tool level |
| Agent decision recording | ❌ Không có — quality_review decisions không được persist |
| Time-series tracking across runs | ❌ Không có — mỗi run log riêng, không aggregate |
| Chain-of-thought capture | ❌ Không có — `<think>` blocks bị strip trước khi log |

**Gap cụ thể:**

```python
# Hiện tại: log là append-only plaintext
logger.info("[SG3] Financial analysis complete")  # không có structured data

# Cần: structured audit event
audit.record({
    "event": "node_complete",
    "node": "analyze_financial",
    "run_id": run_id,
    "timestamp": "2026-04-20T10:23:15Z",
    "duration_ms": 45230,
    "input": {"pdf_dir": "...", "company": "mst"},
    "output": {"years_extracted": [2022, 2023, 2024], "total_assets_2024": 1750574},
    "tool_calls": [
        {"tool": "extract_pdf_financial_tables", "strategy": "vision_llm", "cache_hit": False},
        {"tool": "calculate_financial_ratios", "status": "ok"}
    ],
    "decisions": {"retry_triggered": False, "quality_score": None}
})
```

**LangSmith là closest thing** nhưng: (1) cần API key/paid tier cho production, (2) chain-of-thought bị strip trước khi ghi log, (3) `<think>` blocks của Qwen bị xóa ở `strip_llm_json()` — mất reasoning trace.

---

### 5. Multi-Agent Orchestration Patterns

**Yêu cầu:** Pipeline, fan-out/fan-in, conditional routing based on intermediate results.

**Hiện tại — đây là phần mạnh nhất:**

| Pattern | Trạng thái |
|---|---|
| Sequential pipeline | ✅ Có — `extract → sector/financial → assemble → review` |
| Parallel fan-out/fan-in | ✅ Có — `analyze_sector ‖ analyze_financial` |
| Conditional routing | ✅ Có — `route_after_review()` → retry hoặc END |
| Self-correction loop | ✅ Có — retry với `quality_feedback` hint, max 1 lần |
| Annotated reducers (safe parallel writes) | ✅ Có — `errors`, `messages`, `current_step` |
| Dynamic routing based on intermediate results | ⚠️ Partial — chỉ sau QR, không phải mid-pipeline |
| Agent-to-agent handoff với validation | ❌ Không có |
| Timeout per agent | ❌ Không có |
| Dead letter queue | ❌ Không có — failed nodes bị skip silently |

**Gap quan trọng:**

```python
# Hiện tại: routing chỉ ở cuối (sau QR)
def route_after_review(state):
    score = state['quality_review_result']['score']
    if score >= 7 or state['retry_count'] >= 2:
        return END
    # ... route to weakest section

# Cần: routing mid-pipeline dựa trên intermediate results
def route_after_extraction(state):
    company_info = state.get('company_info')
    if not company_info or not company_info.main_business:
        return "fallback_manual_input"   # không có industry → SG2 không thể chạy
    if company_info.main_business in EXCLUDED_INDUSTRIES:
        return "rejection_node"           # ngành bị hạn chế tín dụng
    return "analyze_sector_and_financial"

# Timeout: SG3 Vision OCR có thể mất 5+ phút
# Nếu > timeout → fallback về cached result hoặc skip year
```

---

## Những gì dự án làm TỐT (đáng giữ lại)

| Component | Đánh giá |
|---|---|
| Parallel fan-out/fan-in (LangGraph) | Foundation tốt — chạy SG2‖SG3 đồng thời |
| `invoke_with_retry` với smart backoff | Circuit breaker primitive — parse "try again in Xs" |
| OCR cache versioned | Checkpoint pattern đúng — cần generalize cho all nodes |
| `state['errors']` Annotated list | Error accumulation tốt — cần trigger circuit breaker |
| `@timed_node` decorator | Telemetry foundation — cần extend thành structured audit |
| Conditional routing `route_after_review` | Orchestration pattern đúng — cần thêm mid-pipeline |
| PDF 4-strategy graceful degradation | Fallback pattern chuẩn |

---

## Những gì dự án làm CHƯA TỐT

| Vấn đề | Impact | Ví dụ |
|---|---|---|
| Silent failure propagation | Cao | SG3 lấy 0 năm BCTC → assembler tạo report rỗng section 3 |
| No circuit breaker trên anomalous data | Cao | total_assets = 0 → ratios = None → LLM viết "không có dữ liệu" |
| Checkpoint chỉ cho OCR, không cho agent outputs | Trung | Re-run phải redo SG1+SG2 dù chỉ SG3 fail |
| Audit trail là plaintext, không queryable | Trung | Không thể hỏi "tất cả run có quality_score < 5 tháng này" |
| Cross-agent validation không có | Trung | SG2 có thể analyze sai ngành nếu SG1 extract nhầm |
| Timeout không có | Thấp | Vision OCR treo → pipeline block vô hạn |

---

## Khuyến nghị implementation

### Priority 1 — Circuit Breaker (impact cao nhất, effort vừa)

Thêm `src/utils/circuit_breaker.py`:

```python
class CircuitBreaker:
    def __init__(self, thresholds: dict):
        self.thresholds = thresholds
        self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN

    def check(self, node_name: str, output: dict) -> CircuitBreakerResult:
        """Returns: ok | trip(reason, fallback) | warn(reason)"""
        rules = self.thresholds.get(node_name, [])
        for rule in rules:
            if rule.is_violated(output):
                return CircuitBreakerResult.trip(rule.reason, rule.fallback)
        return CircuitBreakerResult.ok()
```

**Thresholds ban đầu (low-hanging fruit):**
```yaml
analyze_financial:
  - field: total_assets
    condition: "== 0 or is None"
    action: trip
    fallback: use_cached_or_skip
  - field: years_extracted
    condition: "len < 1"
    action: trip
    fallback: use_cached_or_skip
  - field: revenue_yoy_growth
    condition: "> 500%"
    action: warn
    message: "Unusual revenue growth, verify data"

analyze_sector:
  - field: section_2_sector
    condition: "len < 200"
    action: trip
    fallback: use_llm_knowledge_fallback
```

### Priority 2 — Checkpoint generalization

Generalize OCR cache pattern sang tất cả nodes. Thêm `src/utils/checkpoint.py` với interface tương tự `ocr_cache.py`:

```python
def save_node_checkpoint(run_id: str, node: str, state_slice: dict): ...
def load_node_checkpoint(run_id: str, node: str) -> Optional[dict]: ...
def rollback_to(run_id: str, node: str) -> AgentState: ...
```

Implement `run_id` concept trong `AgentState` — hiện tại mỗi run không có ID duy nhất.

### Priority 3 — Structured audit trail

Thay file log plaintext bằng JSONL append:

```python
# logs/audit_YYYYMMDD.jsonl — 1 JSON object per line, queryable với jq/pandas
{"ts": "...", "run_id": "...", "event": "node_start", "node": "analyze_financial", ...}
{"ts": "...", "run_id": "...", "event": "tool_call", "tool": "extract_pdf", "strategy": "vision_llm", ...}
{"ts": "...", "run_id": "...", "event": "node_end", "node": "analyze_financial", "duration_ms": 45230, ...}
```

Giữ `<think>` blocks (không strip) trong audit log — chỉ strip khi parse JSON output.

### Priority 4 — Cross-agent validation gate

Thêm validation node sau mỗi subgraph:

```
SG1 → [validate_company_info] → SG2/SG3
SG2 → [validate_sector_output] → assembler
SG3 → [validate_financial_output] → assembler
```

**Validation company info (5 rules, pure Python):**
1. `company_name` không rỗng
2. `main_business` không rỗng (SG2 phụ thuộc)
3. `tax_code` match `^\d{10}(-\d{3})?$` (MST Việt Nam)
4. `shareholders` có ít nhất 1 phần tử với `ownership_pct > 0`
5. `established_date` không future date

---

## Effort thực tế vs. estimate

Estimate **5-10 tháng** là hợp lý vì:

| Effort item | Lý do |
|---|---|
| Circuit breaker calibration | LLM non-deterministic → threshold "anomaly" khó định nghĩa tuyệt đối |
| Distributed systems consistency | Parallel fan-out + checkpoint = distributed state management |
| Real-time latency | Vision OCR 45s/trang mâu thuẫn với circuit breaker timeout |
| LLM output variance | Cùng input, output khác nhau 10% mỗi run → false positive circuit trips |
| Audit trail storage | JSONL OK cho MVP, nhưng production cần time-series DB (InfluxDB/ClickHouse) |

**Với dự án hiện tại (interview context):** Circuit breaker stub (2 rules: total_assets=0 và section length < 200) + JSONL audit log là đủ để demonstrate concept. Implement được trong 1 ngày, không break existing pipeline.

---

## So sánh với Pain Points được giải quyết

**Pain Point 1 (workflow không ổn định, cascading failure):**
- Hiện tại: `invoke_with_retry` xử lý rate limits, nhưng không có circuit breaker thực sự
- Gap chính: silent failure propagation — downstream nodes nhận input rỗng không biết
- Fix: Circuit breaker + validation gate giữa nodes

**Pain Point 2 (khó debug, không có audit trail):**
- Hiện tại: log file tốt cho debugging thủ công, LangSmith optional
- Gap chính: không queryable, không structured, CoT bị strip
- Fix: JSONL audit trail + giữ `<think>` blocks trong audit

---

## Kết luận

Pipeline LangGraph hiện tại có **foundation orchestration tốt** (parallel, conditional routing, self-correction) nhưng thiếu 3 trong 5 yêu cầu cốt lõi:

1. **Circuit Breaker** — cần thêm anomaly detection + abort logic; `invoke_with_retry` chỉ handle rate limits
2. **Checkpoint/Rollback** — OCR cache là đúng pattern nhưng chỉ cover 1/5 nodes; cần generalize
3. **Cross-Agent Validation** — balance sheet check tốt nhưng chỉ sau extraction; cần validation gate giữa nodes
4. **Audit Trail** — log file có nhưng không queryable; `<think>` blocks bị strip mất CoT
5. **Orchestration Patterns** — đây là **điểm mạnh nhất** của dự án; fan-out/fan-in và conditional routing đã implement đúng
