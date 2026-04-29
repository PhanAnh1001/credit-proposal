# Review: Domain-Grounded Verification Agent

**Ngày review:** 2026-04-20  
**Branch:** `claude/domain-verification-agent-WDmUD`  
**Scope:** Đánh giá dự án Credit Proposal AI Agent theo yêu cầu Domain-Grounded Verification Agent

---

## Tổng quan hiện trạng

Dự án hiện tại là một Credit Proposal AI Agent với pipeline LangGraph 5-node, tập trung vào **extraction** và **generation**. Verification hiện tại rất sơ khai so với yêu cầu của một Domain-Grounded Verification Agent.

**Pipeline hiện tại:**
```
extract_company_info
    ↓
[analyze_sector] ← parallel → [analyze_financial]
    ↓
assemble_report
    ↓
quality_review (LLM-as-Judge, overall score)
    ↓ (conditional routing)
END hoặc retry (tối đa 1 lần)
```

---

## Gap Analysis theo từng yêu cầu

### 1. Domain Knowledge Base Integration

**Yêu cầu:** Connect với regulatory database, internal policy, compliance knowledge. Auto-update khi rules thay đổi.

**Hiện tại:**
- Threshold tĩnh hardcode trong prompt strings: `Current Ratio ≥ 1.5`, `ROE ≥ 10%`, `D/E ≤ 1.5`
- Không có database quy định nào — VAS, NHNN circulars, Basel III/IV đều vắng mặt
- Không có cơ chế auto-update khi rules thay đổi

**Gap:**
- Không có industry-specific benchmarks (xây dựng vs bán lẻ vs sản xuất có threshold khác nhau)
- Không có NHNN Circular 11/2021 (phân loại nợ), Circular 39/2016 (cho vay), hay Basel II/III risk weight rules
- Thresholds hiện tại là "general rule of thumb", không phải banking domain rules
- Nếu NHNN thay đổi quy định → phải sửa code thủ công

**Cần thêm:**
```
knowledge/
  rules/
    financial_thresholds.yaml    # industry-specific benchmarks
    nhnn_regulations.yaml        # NHNN circulars
    vas_standards.yaml           # VAS accounting rules
    reasonableness_bounds.yaml   # YoY change limits, etc.
  loader.py                      # loads rules, cho phép hot-reload
```

---

### 2. Claim-Level Confidence Scoring

**Yêu cầu:** Detect sai lệch nghiệp vụ cụ thể (ví dụ: tính sai hệ số rủi ro Basel). Score per claim, không phải overall.

**Hiện tại:**
- `quality_review_node()` trả về **overall score 0–10** + component scores (completeness, sector_quality, financial_quality)
- Không có per-claim scoring
- LLM-as-Judge (gpt-oss-20b) liệt kê issues nhưng không gắn confidence cho từng claim
- Ratio calculation là deterministic Python — không có confidence concept

**Gap — lớn nhất trong 4 yêu cầu:**

```python
# Hiện tại (coarse-grained):
quality_review_result = {
    "score": 7.5,
    "financial_quality": 7,
    "issues": ["Missing YoY comparison"]
}

# Cần (claim-level):
claims = [
    {
        "claim": "ROE = 15.2%",
        "confidence": 0.95,
        "source": "KQKD 2024 line 120",
        "verified": True
    },
    {
        "claim": "D/E thấp hơn ngành",
        "confidence": 0.3,
        "source": None,
        "verified": False,
        "issue": "Không có industry benchmark để so sánh"
    },
    {
        "claim": "Rủi ro tín dụng thấp",
        "confidence": 0.2,
        "source": None,
        "verified": False,
        "issue": "Claim không có số liệu hỗ trợ"
    }
]
```

- Không thể phát hiện sai lệch như tính sai Basel risk weight vì không có Basel rules
- Không có traceability từ claim → source line trong PDF

---

### 3. Intelligent Escalation

**Yêu cầu:** Khi confidence thấp → escalate cho human với context đầy đủ (alternative interpretations, relevant regulations, similar cases).

**Hiện tại:**
- Self-correction loop: retry tối đa 1 lần nếu score < 7
- Nếu sau retry vẫn thấp → END (không có escalation)
- `quality_feedback` là text hint cho LLM retry, không phải escalation context cho human

**Gap:**
```
Luồng hiện tại:  score < 7 → retry → END

Luồng cần thiết: score < 7 → retry → [still < 7] → escalate to human
                 confidence < threshold trên bất kỳ claim → escalate với:
                   - alternative interpretations
                   - relevant regulations
                   - similar historical cases
```

- Không có human-in-the-loop pathway
- Không có "alternative interpretations" — chỉ có 1 LLM output duy nhất
- Không có similar cases lookup (cần vector store hoặc case database)

---

### 4. Multi-Layer Verification

**Yêu cầu:** Syntax check → Domain rules → Regulatory check → Reasonableness check.

**Hiện tại — chỉ có 1 layer thực sự:**

| Layer | Yêu cầu | Hiện trạng |
|---|---|---|
| **Syntax check** | Format, completeness | ❌ Không có riêng biệt |
| **Domain rules** | Industry thresholds, VAS compliance | ❌ Threshold static trong prompt |
| **Regulatory check** | NHNN, Basel, SBV | ❌ Vắng mặt hoàn toàn |
| **Reasonableness check** | Revenue ↑300% suspicious, margin anomaly | ⚠️ Một phần — LLM prompt yêu cầu flag anomalies nhưng không systematic |

**`validate_balance_sheet()` hiện tại:**
```python
# Chỉ check 1 accounting identity:
total_assets ≈ total_liabilities + equity  (±2% tolerance)
```
Đây là accounting sanity check, không phải domain verification.

**Cần thêm:**
```
Layer 1 - Syntax:       Tất cả required fields có giá trị? Format hợp lệ?
Layer 2 - Domain:       ROE, D/E, liquidity so với industry benchmark?
Layer 3 - Regulatory:   NHNN classification criteria? Basel capital adequacy?
Layer 4 - Reasonableness: YoY changes có outlier? Internal consistency?
```

---

## Những gì dự án làm TỐT (đáng giữ lại)

| Component | Đánh giá |
|---|---|
| Balance sheet cross-validation (±2%) | Nền tảng tốt cho Layer 1, cần expand |
| Deterministic ratio calculation (Python thuần) | Tránh hallucination số — pattern đúng |
| Self-correction loop | Foundation cho escalation, cần extend |
| LLM-as-Judge architecture | Cần granularize thành claim-level |
| Unit normalization (`_normalize_units()`) | Reasonableness check primitive tốt |
| Multi-strategy PDF extraction với caching | Không liên quan verification nhưng tốt |

---

## Khuyến nghị implementation

### Priority 1 — Claim-level scoring (impact cao nhất)

Thêm `src/models/verification.py`:

```python
class ClaimVerification(BaseModel):
    claim_text: str
    claim_type: Literal["financial_fact", "ratio", "trend", "sector_claim", "risk_assessment"]
    confidence: float          # 0.0 – 1.0
    source_reference: Optional[str]  # "CDKT 2024 line A100"
    verified: bool
    issues: list[str]
    regulation_refs: list[str] # "NHNN Circular 11/2021 Article 3"
```

Thêm `src/agents/verification_agent.py` — node chạy sau `assemble_report`, trước `quality_review`.

### Priority 2 — Domain knowledge base

```
src/knowledge/
  rules/
    financial_thresholds.yaml
    nhnn_regulations.yaml
    vas_standards.yaml
    reasonableness_bounds.yaml
  loader.py
```

Tách rules khỏi code: thay đổi quy định không cần redeploy.

### Priority 3 — Multi-layer verifier

Thêm `src/tools/multi_layer_verifier.py`:

```python
def verify_syntax(state) -> list[VerificationResult]: ...
def verify_domain_rules(state, rules_db) -> list[VerificationResult]: ...
def verify_regulatory(state, reg_db) -> list[VerificationResult]: ...
def verify_reasonableness(state) -> list[VerificationResult]: ...
```

Chạy sequential, kết quả feed vào claim-level scoring.

### Priority 4 — Escalation pathway

Extend routing logic trong `graph.py`:

```python
def route_after_verification(state):
    low_confidence = [c for c in state.claims if c.confidence < 0.5]
    if len(low_confidence) > 3:
        return "human_escalation"   # node mới — format escalation report
    elif state.quality_score < 7 and state.retry_count < 1:
        return "retry"
    else:
        return END
```

---

## Effort thực tế vs. estimate

Estimate **6–12 tháng** trong yêu cầu là **hợp lý** vì:

| Effort item | Lý do |
|---|---|
| Knowledge base maintenance | NHNN ban hành 20–30 thông tư/năm; cần legal team review |
| "Ai kiểm tra người kiểm tra" | Domain rules cần human expert validation |
| Industry benchmarks | Cần data từ Bloomberg, FiinGroup, SBV reports — không public free |
| Basel risk weight | Nhiều interpretation tùy loại tài sản; ambiguity cao |
| Similar cases lookup | Cần historical case database — data privacy issues |

**Với dự án hiện tại (interview context):** Phase 1 khả thi trong 1–2 ngày — thêm multi-layer verifier + granularize quality review thành claim-level, knowledge base dưới dạng stub YAML. Đủ để demonstrate kiến trúc đúng và hiểu gap.

---

## Kết luận

Dự án hiện tại là một **generation pipeline** tốt, không phải **verification agent**. Để đáp ứng yêu cầu Domain-Grounded Verification Agent, delta cần thêm:

1. `ClaimVerification` model — per-claim confidence thay vì overall score
2. Structured knowledge base tách khỏi code — hot-reload khi rules thay đổi
3. 4-layer verifier pipeline — thay vì 1-layer balance sheet check
4. Human escalation node — thay vì chỉ LLM retry

**Pain points được giải quyết:** Pain Point 4 (sai lệch nghiệp vụ không được phát hiện) và Pain Point 5 (thiếu regulatory compliance check) — cả hai đều cần domain knowledge bên ngoài mà generation pipeline không có.
