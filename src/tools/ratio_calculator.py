"""Pure-Python financial ratio calculator — no LLM calls."""
from typing import Optional


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def safe_div(
    numerator: Optional[float],
    denominator: Optional[float],
    decimals: int = 4,
) -> Optional[float]:
    """Divide two numbers, returning None when the result is undefined.

    Returns None if either argument is None or if the denominator is zero.
    The result is rounded to ``decimals`` decimal places.
    """
    if numerator is None or denominator is None or denominator == 0:
        return None
    return round(numerator / denominator, decimals)


# ---------------------------------------------------------------------------
# Ratio calculation
# ---------------------------------------------------------------------------

def calculate_financial_ratios(statements: dict[int, dict]) -> dict[int, dict]:
    """Calculate financial ratios for every year present in *statements*.

    Args:
        statements: Mapping of {year: FinancialStatement-compatible dict}.
                    The dicts must use the same field names as
                    ``FinancialStatement`` (snake_case English names).

    Returns:
        Mapping of {year: FinancialRatios-compatible dict}.
        Each value contains a ``year`` key and all ratio fields
        (``None`` where the underlying data is unavailable).
    """
    ratios: dict[int, dict] = {}
    years_sorted = sorted(statements.keys())

    for i, year in enumerate(years_sorted):
        s = statements[year]
        r: dict = {"year": year}

        # ------------------------------------------------------------------
        # Liquidity ratios
        # ------------------------------------------------------------------
        current_assets = s.get("current_assets")
        current_liabilities = s.get("current_liabilities")
        inventories = s.get("inventories") or 0.0

        r["current_ratio"] = safe_div(current_assets, current_liabilities)

        if current_assets is not None and current_liabilities:
            r["quick_ratio"] = safe_div(current_assets - inventories, current_liabilities)
        else:
            r["quick_ratio"] = None

        # ------------------------------------------------------------------
        # Leverage / solvency ratios
        # ------------------------------------------------------------------
        total_liabilities = s.get("total_liabilities")
        total_assets = s.get("total_assets")
        equity = s.get("equity")

        r["debt_to_equity"] = safe_div(total_liabilities, equity)
        r["debt_to_assets"] = safe_div(total_liabilities, total_assets)

        # ------------------------------------------------------------------
        # Profitability ratios (expressed as percentages)
        # ------------------------------------------------------------------
        net_profit = s.get("net_profit")
        net_revenue = s.get("net_revenue")
        gross_profit = s.get("gross_profit")

        roe = safe_div(net_profit, equity)
        r["roe"] = round(roe * 100, 2) if roe is not None else None

        roa = safe_div(net_profit, total_assets)
        r["roa"] = round(roa * 100, 2) if roa is not None else None

        npm = safe_div(net_profit, net_revenue)
        r["net_profit_margin"] = round(npm * 100, 2) if npm is not None else None

        gpm = safe_div(gross_profit, net_revenue)
        r["gross_profit_margin"] = round(gpm * 100, 2) if gpm is not None else None

        # ------------------------------------------------------------------
        # Year-over-year growth ratios (require a previous year)
        # ------------------------------------------------------------------
        if i > 0:
            prev_year = years_sorted[i - 1]
            prev_s = statements[prev_year]

            prev_revenue = prev_s.get("net_revenue")
            if prev_revenue and prev_revenue != 0 and net_revenue is not None:
                r["revenue_growth_yoy"] = round(
                    (net_revenue - prev_revenue) / abs(prev_revenue) * 100, 2
                )
            else:
                r["revenue_growth_yoy"] = None

            prev_profit = prev_s.get("net_profit")
            if prev_profit and prev_profit != 0 and net_profit is not None:
                r["net_profit_growth_yoy"] = round(
                    (net_profit - prev_profit) / abs(prev_profit) * 100, 2
                )
            else:
                r["net_profit_growth_yoy"] = None
        else:
            r["revenue_growth_yoy"] = None
            r["net_profit_growth_yoy"] = None

        ratios[year] = r

    return ratios


# ---------------------------------------------------------------------------
# Balance-sheet cross-validation
# ---------------------------------------------------------------------------

def validate_balance_sheet(statement: dict) -> list[str]:
    """Check that Total Assets ≈ Total Liabilities + Equity.

    A 2 % tolerance is applied to account for rounding differences between
    the LLM-extracted numbers and the original PDF values.

    Args:
        statement: A FinancialStatement-compatible dict for a single year.

    Returns:
        A list of human-readable error strings.  Empty list means the
        balance sheet balances within tolerance.
    """
    errors: list[str] = []

    total_assets = statement.get("total_assets")
    total_liabilities = statement.get("total_liabilities")
    equity = statement.get("equity")

    if total_assets is None or total_liabilities is None or equity is None:
        return errors  # Cannot validate with missing data

    reconstructed = total_liabilities + equity
    tolerance = abs(total_assets) * 0.02  # 2 % of total assets

    if abs(total_assets - reconstructed) > tolerance:
        errors.append(
            f"Balance sheet mismatch for year {statement.get('year', '?')}: "
            f"Total Assets = {total_assets:,.0f} "
            f"vs Liabilities + Equity = {reconstructed:,.0f} "
            f"(difference = {abs(total_assets - reconstructed):,.0f})"
        )

    return errors
