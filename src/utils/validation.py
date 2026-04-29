"""Cross-agent validation gate — pure Python, no LLM calls.

Each validator checks the structured output of one subgraph before downstream
nodes consume it. Failures are logged as warnings, not hard errors, so the
pipeline continues with known-bad data flagged rather than aborting.

Usage:
    failures = validate_company_info(company_info)
    failures = validate_sector_output(section_2_text)
    failures = validate_financial_output(financial_data)
"""

import re
from datetime import date, datetime
from typing import Optional

from ..knowledge.loader import get_reasonableness_bounds, get_financial_thresholds
from .logger import get_logger

logger = get_logger("validation")

# Vietnam MST: 10 digits, optionally followed by -NNN branch code
_TAX_CODE_RE = re.compile(r"^\d{10}(-\d{3})?$")


def validate_company_info(company_info) -> list[str]:
    """5 validation rules for SG1 output.

    Returns list of failure messages (empty = all pass).
    """
    if company_info is None:
        return ["company_info is None"]

    failures: list[str] = []

    # Rule 1: company_name must not be empty
    if not getattr(company_info, "company_name", None):
        failures.append("company_name is empty")

    # Rule 2: main_business required (SG2 depends on it for sector classification)
    if not getattr(company_info, "main_business", None):
        failures.append("main_business is empty — SG2 sector analysis may be inaccurate")

    # Rule 3: tax_code format (Vietnam MST)
    tax_code = getattr(company_info, "tax_code", None)
    if tax_code and not _TAX_CODE_RE.match(str(tax_code).strip()):
        failures.append(f"tax_code format invalid: '{tax_code}' (expected 10 digits or 10-3 digits)")

    # Rule 4: at least 1 shareholder with positive ownership (if shareholders provided)
    shareholders = getattr(company_info, "shareholders", []) or []
    if shareholders:
        positive_pct = [
            s for s in shareholders
            if getattr(s, "percentage", None) and getattr(s, "percentage") > 0
        ]
        if not positive_pct:
            failures.append("No shareholder with positive ownership percentage found")

    # Rule 5: established_date must not be in the future
    established = getattr(company_info, "established_date", None)
    if established:
        try:
            # Parse common date formats
            for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y"):
                try:
                    parsed = datetime.strptime(str(established).strip(), fmt).date()
                    if parsed > date.today():
                        failures.append(f"established_date '{established}' is in the future")
                    break
                except ValueError:
                    continue
        except Exception:
            pass  # unparseable date — not a blocker

    if failures:
        for f in failures:
            logger.warning(f"[validate_company_info] FAIL: {f}")
    else:
        logger.debug("[validate_company_info] All 5 rules PASS")

    return failures


def validate_sector_output(section_2_sector: Optional[str]) -> list[str]:
    """Validate SG2 output before it reaches the assembler.

    Returns list of failure messages.
    """
    bounds = get_reasonableness_bounds().get("sector_analysis", {})
    min_len = bounds.get("min_section_length", 200)
    min_risks = bounds.get("min_risk_count", 2)

    failures: list[str] = []
    text = section_2_sector or ""

    # Rule 1: length
    if len(text) < min_len:
        failures.append(f"sector section too short ({len(text)} chars < {min_len})")

    # Rule 2: risk mentions
    risk_keywords = ["rủi ro", "risk", "thách thức", "challenge", "nguy cơ"]
    risk_count = sum(text.lower().count(kw) for kw in risk_keywords)
    if risk_count < min_risks:
        failures.append(f"sector section mentions too few risks ({risk_count} < {min_risks})")

    if failures:
        for f in failures:
            logger.warning(f"[validate_sector_output] FAIL: {f}")
    else:
        logger.debug("[validate_sector_output] All rules PASS")

    return failures


def validate_financial_output(financial_data, sector: Optional[str] = None) -> list[str]:
    """Validate SG3 output before it reaches the assembler.

    Returns list of failure messages.
    """
    if financial_data is None:
        return ["financial_data is None"]

    bounds = get_reasonableness_bounds().get("financial_data", {})
    thresholds = get_financial_thresholds(sector)
    failures: list[str] = []

    statements = getattr(financial_data, "statements", {}) or {}
    ratios = getattr(financial_data, "ratios", {}) or {}

    # Rule 1: at least 1 year extracted
    min_years = bounds.get("min_years_required", 1)
    if len(statements) < min_years:
        failures.append(f"Only {len(statements)} year(s) extracted, expected ≥{min_years}")

    for year, stmt in statements.items():
        total_assets = getattr(stmt, "total_assets", None)
        equity = getattr(stmt, "equity", None)
        net_revenue = getattr(stmt, "net_revenue", None)

        # Rule 2: required non-zero fields
        for field_name, val in [("total_assets", total_assets), ("equity", equity), ("net_revenue", net_revenue)]:
            if val is None or val == 0:
                failures.append(f"Year {year}: {field_name} is {val} — may indicate OCR failure")

    # Rule 3: ratio sanity bounds (warn only — not hard fail)
    max_cr = thresholds.get("current_ratio", {}).get("max_suspicious", 20.0)
    for year, ratio in ratios.items():
        cr = getattr(ratio, "current_ratio", None)
        if cr is not None and cr > max_cr:
            failures.append(f"Year {year}: current_ratio = {cr:.2f} > {max_cr} — possible data error")

        roe = getattr(ratio, "roe", None)
        if roe is not None and roe < thresholds.get("roe", {}).get("min_acceptable", -100):
            failures.append(f"Year {year}: ROE = {roe:.1f}% is below minimum acceptable")

        gross_margin = getattr(ratio, "gross_profit_margin", None)
        if gross_margin is not None and gross_margin < 0:
            failures.append(f"Year {year}: gross_profit_margin = {gross_margin:.1f}% < 0 — selling below cost")

    if failures:
        for f in failures:
            logger.warning(f"[validate_financial_output] FAIL: {f}")
    else:
        logger.debug("[validate_financial_output] All rules PASS")

    return failures
