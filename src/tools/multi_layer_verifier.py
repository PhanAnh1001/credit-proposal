"""Multi-layer verifier for assembled credit proposal report.

Runs 4 sequential verification layers on the assembled report + structured data:
  Layer 1 - Syntax:       Required fields have values? Formats valid?
  Layer 2 - Domain:       Ratios vs industry benchmarks (from YAML)?
  Layer 3 - Regulatory:   NHNN classification stub (placeholder — needs legal team)
  Layer 4 - Reasonableness: YoY outliers? Internal consistency? Cross-field checks?

Each layer returns a list of VerificationResult objects. Results are aggregated
and fed into quality_review as additional context.

This runs after assemble_report, before (or alongside) quality_review.
"""

from dataclasses import dataclass, field
from typing import Optional

from ..knowledge.loader import get_financial_thresholds, get_reasonableness_bounds
from ..utils.logger import get_logger

logger = get_logger("multi_layer_verifier")


@dataclass
class VerificationResult:
    layer: str           # "syntax" | "domain" | "regulatory" | "reasonableness"
    field: str           # which field or claim
    passed: bool
    message: str
    severity: str = "warn"  # "warn" | "error"


def verify_syntax(state: dict) -> list[VerificationResult]:
    """Layer 1: Required fields present and non-empty."""
    results: list[VerificationResult] = []

    def check(field_name: str, value, required: bool = True):
        ok = value is not None and str(value).strip() != ""
        if not ok and required:
            results.append(VerificationResult(
                layer="syntax", field=field_name, passed=False,
                message=f"{field_name} is missing or empty",
                severity="error",
            ))
        elif ok:
            results.append(VerificationResult(
                layer="syntax", field=field_name, passed=True,
                message=f"{field_name} present",
            ))
        return ok

    company_info = state.get("company_info")
    financial_data = state.get("financial_data")

    if company_info:
        check("company_name", getattr(company_info, "company_name", None))
        check("tax_code", getattr(company_info, "tax_code", None), required=False)
        check("main_business", getattr(company_info, "main_business", None))
    else:
        results.append(VerificationResult(
            layer="syntax", field="company_info", passed=False,
            message="company_info object is None", severity="error",
        ))

    if financial_data:
        stmts = getattr(financial_data, "statements", {}) or {}
        for year, stmt in stmts.items():
            check(f"total_assets_{year}", getattr(stmt, "total_assets", None))
            check(f"equity_{year}", getattr(stmt, "equity", None))
            check(f"net_revenue_{year}", getattr(stmt, "net_revenue", None))
    else:
        results.append(VerificationResult(
            layer="syntax", field="financial_data", passed=False,
            message="financial_data object is None", severity="error",
        ))

    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)
    logger.debug(f"[Layer 1 Syntax] {passed} pass, {failed} fail")
    return results


def verify_domain_rules(state: dict) -> list[VerificationResult]:
    """Layer 2: Domain-specific thresholds from YAML knowledge base."""
    results: list[VerificationResult] = []
    financial_data = state.get("financial_data")
    if not financial_data:
        return results

    company_info = state.get("company_info")
    sector = getattr(company_info, "main_business", None) if company_info else None
    thresholds = get_financial_thresholds(sector)
    ratios_data = getattr(financial_data, "ratios", {}) or {}

    for year, ratio in ratios_data.items():
        # Current ratio
        cr = getattr(ratio, "current_ratio", None)
        if cr is not None:
            cr_thresh = thresholds.get("current_ratio", {})
            if cr < cr_thresh.get("min_acceptable", 1.0):
                results.append(VerificationResult(
                    layer="domain", field=f"current_ratio_{year}", passed=False,
                    message=f"Year {year}: current_ratio={cr:.2f} < min_acceptable={cr_thresh.get('min_acceptable', 1.0)}",
                    severity="warn",
                ))
            elif cr >= cr_thresh.get("healthy", 1.5):
                results.append(VerificationResult(
                    layer="domain", field=f"current_ratio_{year}", passed=True,
                    message=f"Year {year}: current_ratio={cr:.2f} ≥ healthy threshold",
                ))
            else:
                results.append(VerificationResult(
                    layer="domain", field=f"current_ratio_{year}", passed=True,
                    message=f"Year {year}: current_ratio={cr:.2f} acceptable (below healthy but above min)",
                ))

        # Debt-to-equity
        de = getattr(ratio, "debt_to_equity", None)
        if de is not None:
            de_thresh = thresholds.get("debt_to_equity", {})
            if de > de_thresh.get("max_acceptable", 3.0):
                results.append(VerificationResult(
                    layer="domain", field=f"debt_to_equity_{year}", passed=False,
                    message=f"Year {year}: D/E={de:.2f} > max_acceptable={de_thresh.get('max_acceptable', 3.0)} — highly leveraged",
                    severity="error",
                ))
            elif de > de_thresh.get("healthy_max", 1.5):
                results.append(VerificationResult(
                    layer="domain", field=f"debt_to_equity_{year}", passed=False,
                    message=f"Year {year}: D/E={de:.2f} > healthy_max={de_thresh.get('healthy_max', 1.5)}",
                    severity="warn",
                ))
            else:
                results.append(VerificationResult(
                    layer="domain", field=f"debt_to_equity_{year}", passed=True,
                    message=f"Year {year}: D/E={de:.2f} within healthy range",
                ))

        # ROE
        roe = getattr(ratio, "roe", None)
        if roe is not None:
            roe_thresh = thresholds.get("roe", {})
            if roe < roe_thresh.get("min_acceptable", 0.0):
                results.append(VerificationResult(
                    layer="domain", field=f"roe_{year}", passed=False,
                    message=f"Year {year}: ROE={roe:.1f}% < 0 — company is losing money",
                    severity="error",
                ))
            elif roe < roe_thresh.get("healthy_min", 10.0):
                results.append(VerificationResult(
                    layer="domain", field=f"roe_{year}", passed=False,
                    message=f"Year {year}: ROE={roe:.1f}% below healthy min {roe_thresh.get('healthy_min', 10.0)}%",
                    severity="warn",
                ))
            else:
                results.append(VerificationResult(
                    layer="domain", field=f"roe_{year}", passed=True,
                    message=f"Year {year}: ROE={roe:.1f}% healthy",
                ))

    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)
    logger.debug(f"[Layer 2 Domain] {passed} pass, {failed} fail")
    return results


def verify_regulatory(state: dict) -> list[VerificationResult]:
    """Layer 3: Regulatory compliance stub.

    Full implementation requires legal team to codify NHNN Circular 11/2021
    (loan classification), Circular 39/2016 (lending rules), and Basel II/III
    risk weight tables. These are placeholders to demonstrate the architecture.
    """
    results: list[VerificationResult] = []
    financial_data = state.get("financial_data")
    if not financial_data:
        return results

    stmts = getattr(financial_data, "statements", {}) or {}
    ratios_data = getattr(financial_data, "ratios", {}) or {}

    # Stub Rule 1: Debt-to-assets ratio (proxy for capital adequacy concept)
    for year, ratio in ratios_data.items():
        da = getattr(ratio, "debt_to_assets", None)
        if da is not None:
            if da > 0.9:
                results.append(VerificationResult(
                    layer="regulatory", field=f"debt_to_assets_{year}", passed=False,
                    message=f"[STUB] Year {year}: debt/assets={da:.2f} > 90% — potential capital adequacy concern (full Basel III check requires legal team)",
                    severity="warn",
                ))
            else:
                results.append(VerificationResult(
                    layer="regulatory", field=f"debt_to_assets_{year}", passed=True,
                    message=f"[STUB] Year {year}: debt/assets={da:.2f} — within basic threshold",
                ))

    # Stub Rule 2: Profitability for NHNN Group 1 classification
    for year, stmt in stmts.items():
        net_profit = getattr(stmt, "net_profit", None)
        if net_profit is not None and net_profit < 0:
            results.append(VerificationResult(
                layer="regulatory", field=f"net_profit_{year}", passed=False,
                message=f"[STUB] Year {year}: net_profit < 0 — may affect NHNN Circular 11/2021 debt classification (Group 2+)",
                severity="warn",
            ))

    logger.debug(f"[Layer 3 Regulatory] {len(results)} results (stub implementation)")
    return results


def verify_reasonableness(state: dict) -> list[VerificationResult]:
    """Layer 4: Reasonableness checks — YoY outliers, internal consistency."""
    results: list[VerificationResult] = []
    financial_data = state.get("financial_data")
    if not financial_data:
        return results

    bounds = get_reasonableness_bounds().get("financial_data", {})
    yoy_cfg = bounds.get("yoy_warn_thresholds", {})
    max_rev_growth = yoy_cfg.get("revenue_growth_pct_max", 500.0)
    min_rev_growth = yoy_cfg.get("revenue_growth_pct_min", -80.0)

    ratios_data = getattr(financial_data, "ratios", {}) or {}
    stmts = getattr(financial_data, "statements", {}) or {}

    # Rule 1: Revenue YoY outliers
    for year, ratio in ratios_data.items():
        growth = getattr(ratio, "revenue_growth_yoy", None)
        if growth is None:
            continue
        if growth > max_rev_growth or growth < min_rev_growth:
            results.append(VerificationResult(
                layer="reasonableness", field=f"revenue_growth_yoy_{year}", passed=False,
                message=f"Year {year}: revenue YoY = {growth:.1f}% is outside [{min_rev_growth}, {max_rev_growth}]% bounds",
                severity="warn",
            ))
        else:
            results.append(VerificationResult(
                layer="reasonableness", field=f"revenue_growth_yoy_{year}", passed=True,
                message=f"Year {year}: revenue YoY = {growth:.1f}% within normal bounds",
            ))

    # Rule 2: Balance sheet internal consistency (total_assets ≈ liabilities + equity)
    balance_tol = bounds.get("balance_sheet_tolerance_pct", 2.0) / 100.0
    for year, stmt in stmts.items():
        assets = getattr(stmt, "total_assets", None)
        liabilities = getattr(stmt, "total_liabilities", None)
        equity = getattr(stmt, "equity", None)
        if assets and liabilities and equity:
            expected = liabilities + equity
            diff_pct = abs(assets - expected) / assets if assets else 0
            if diff_pct > balance_tol:
                results.append(VerificationResult(
                    layer="reasonableness", field=f"balance_sheet_{year}", passed=False,
                    message=f"Year {year}: assets={assets:,.0f} ≠ liabilities+equity={expected:,.0f} (diff={diff_pct:.1%})",
                    severity="warn",
                ))
            else:
                results.append(VerificationResult(
                    layer="reasonableness", field=f"balance_sheet_{year}", passed=True,
                    message=f"Year {year}: balance sheet consistent (diff={diff_pct:.2%})",
                ))

    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)
    logger.debug(f"[Layer 4 Reasonableness] {passed} pass, {failed} fail")
    return results


def run_all_layers(state: dict) -> dict:
    """Run all 4 verification layers and return aggregated results.

    Returns dict suitable for merging into AgentState:
        {
            "verification_summary": {...},
            "errors": [warning strings for non-fatal failures]
        }
    """
    all_results: list[VerificationResult] = []
    all_results.extend(verify_syntax(state))
    all_results.extend(verify_domain_rules(state))
    all_results.extend(verify_regulatory(state))
    all_results.extend(verify_reasonableness(state))

    errors_results = [r for r in all_results if not r.passed and r.severity == "error"]
    warn_results = [r for r in all_results if not r.passed and r.severity == "warn"]

    total = len(all_results)
    passed = sum(1 for r in all_results if r.passed)

    logger.info(
        f"Multi-layer verification complete: {passed}/{total} pass  "
        f"errors={len(errors_results)}  warns={len(warn_results)}"
    )

    error_msgs = [f"[verifier][{r.layer}] {r.message}" for r in errors_results]
    warn_msgs = [f"[verifier][{r.layer}][warn] {r.message}" for r in warn_results]

    return {
        "verification_summary": {
            "layers_run": ["syntax", "domain", "regulatory", "reasonableness"],
            "total_checks": total,
            "passed": passed,
            "errors": len(errors_results),
            "warnings": len(warn_results),
            "error_details": error_msgs,
            "warn_details": warn_msgs,
        },
        "errors": error_msgs + warn_msgs,
    }
