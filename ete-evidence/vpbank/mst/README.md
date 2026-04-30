# ETE Evidence — vpbank / mst (run 310b8aed)

**Date:** 2026-04-30  
**Run ID:** 310b8aed  
**Pipeline result:** PASS  
**Quality score:** 7/10 (completeness=8, sector=9, financial=4)

## Fix applied this run
`_normalize_units()` cross-field check: if `net_revenue > total_assets × 1000`
(income statement returned raw VND while balance sheet already in millions),
divide all income statement fields by 1,000,000. Triggered for year=2024.

## E2E test results
- `test_full_pipeline_writes_to_bank_scoped_output` — PASS
- `test_unknown_bank_fails_before_graph_runs` — PASS
- CI unit tests (7/7) — PASS

## Artifacts
| File | Description |
|------|-------------|
| `20260430_310b8aed_credit-proposal.docx` | VPBank form DOCX (Output 1) |
| `20260430_310b8aed_credit-analyst-memo.docx` | Analyst memo DOCX (Output 2+3) |
| `20260430_310b8aed_credit-analyst-memo.md` | Full markdown report |
| `20260430_310b8aed_run.log` | Full pipeline log |
