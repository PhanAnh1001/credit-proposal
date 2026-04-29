"""Circuit breaker for LangGraph nodes.

Detects anomalous outputs and prevents cascading failures by tripping early
instead of passing bad data downstream.

Usage:
    breaker = CircuitBreaker()
    result = breaker.check_financial(financial_data, years_extracted)
    if result.tripped:
        return {"errors": [result.reason], "current_step": "circuit_breaker_trip"}

    for w in result.warnings:
        logger.warning(f"[circuit_breaker] {w}")
"""

from dataclasses import dataclass, field
from typing import Optional
from ..knowledge.loader import get_reasonableness_bounds
from .logger import get_logger

logger = get_logger("circuit_breaker")


@dataclass
class CheckResult:
    tripped: bool = False
    reason: str = ""
    warnings: list[str] = field(default_factory=list)

    @classmethod
    def ok(cls, warnings: list[str] | None = None) -> "CheckResult":
        return cls(tripped=False, warnings=warnings or [])

    @classmethod
    def trip(cls, reason: str, warnings: list[str] | None = None) -> "CheckResult":
        return cls(tripped=True, reason=reason, warnings=warnings or [])


class CircuitBreaker:
    """Checks node outputs against anomaly thresholds.

    Rules come from src/knowledge/rules/reasonableness_bounds.yaml so they can
    be updated without touching source code.
    """

    def __init__(self) -> None:
        self._bounds = get_reasonableness_bounds()

    def check_financial(
        self,
        financial_data,  # FinancialData | None
        years_extracted: list[int] | None = None,
    ) -> CheckResult:
        """Check analyze_financial node output for anomalies.

        Rules:
        1. total_assets == 0 or None for ALL years → trip
        2. years_extracted < min_years_required → trip
        3. revenue YoY growth > 500% → warn (potential hallucination)
        4. revenue YoY growth < -80% → warn (unusual decline)
        """
        bounds = self._bounds.get("financial_data", {})
        warnings: list[str] = []

        # Rule 2: no years extracted
        min_years = bounds.get("min_years_required", 1)
        extracted = years_extracted or []
        if len(extracted) < min_years:
            reason = f"Financial extraction failed: only {len(extracted)} year(s) extracted, need ≥{min_years}"
            logger.error(f"[CB TRIP] {reason}")
            return CheckResult.trip(reason)

        if financial_data is None:
            reason = "Financial extraction returned None"
            logger.error(f"[CB TRIP] {reason}")
            return CheckResult.trip(reason)

        statements = getattr(financial_data, "statements", {})

        # Rule 1: total_assets zero/None across all extracted years
        all_zero = all(
            (stmt.total_assets is None or stmt.total_assets == 0)
            for stmt in statements.values()
        )
        if statements and all_zero:
            reason = "total_assets is 0 or None for all extracted years — possible OCR failure"
            logger.error(f"[CB TRIP] {reason}")
            return CheckResult.trip(reason)

        # Rule 3 & 4: revenue YoY anomaly
        yoy_cfg = bounds.get("yoy_warn_thresholds", {})
        max_growth = yoy_cfg.get("revenue_growth_pct_max", 500.0)
        min_growth = yoy_cfg.get("revenue_growth_pct_min", -80.0)

        ratios = getattr(financial_data, "ratios", {})
        for year, ratio in ratios.items():
            growth = getattr(ratio, "revenue_growth_yoy", None)
            if growth is None:
                continue
            if growth > max_growth:
                msg = f"Year {year}: revenue YoY growth = {growth:.1f}% > {max_growth}% — possible hallucination"
                logger.warning(f"[CB WARN] {msg}")
                warnings.append(msg)
            elif growth < min_growth:
                msg = f"Year {year}: revenue YoY growth = {growth:.1f}% < {min_growth}% — unusual decline"
                logger.warning(f"[CB WARN] {msg}")
                warnings.append(msg)

        if not warnings:
            logger.debug(
                f"[CB OK] check_financial: {len(extracted)} years, "
                f"total_assets OK, revenue YoY within bounds"
            )
        return CheckResult.ok(warnings)

    def check_sector(self, section_2_sector: str | None) -> CheckResult:
        """Check analyze_sector node output for anomalies.

        Rule: section length < 200 chars → trip (synthesis failed silently)
        """
        bounds = self._bounds.get("sector_analysis", {})
        min_len = bounds.get("min_section_length", 200)

        text = section_2_sector or ""
        if len(text) < min_len:
            reason = (
                f"Sector section is too short ({len(text)} chars < {min_len}) "
                "— synthesis likely failed"
            )
            logger.error(f"[CB TRIP] {reason}")
            return CheckResult.trip(reason)

        logger.debug(
            f"[CB OK] check_sector: section length {len(text)} chars ≥ {min_len}"
        )
        return CheckResult.ok()

    def check_company_info(self, company_info) -> CheckResult:
        """Lightweight check after SG1 — critical fields for downstream nodes."""
        warnings: list[str] = []

        if company_info is None:
            return CheckResult.trip("company_info is None after extraction")

        if not getattr(company_info, "company_name", None):
            return CheckResult.trip("company_name is empty — SG1 extraction failed")

        if not getattr(company_info, "main_business", None):
            msg = "main_business is empty — SG2 sector analysis may be inaccurate"
            logger.warning(f"[CB WARN] {msg}")
            warnings.append(msg)

        if not warnings:
            logger.debug(
                f"[CB OK] check_company_info: company_name={getattr(company_info, 'company_name', '?')!r}"
            )
        return CheckResult.ok(warnings)
