"""Tests for financial ratio calculator — pure Python, no LLM calls."""
import pytest
from src.tools.ratio_calculator import calculate_financial_ratios, validate_balance_sheet, safe_div


class TestSafeDiv:
    def test_normal_division(self):
        assert safe_div(10.0, 4.0) == 2.5

    def test_zero_denominator(self):
        assert safe_div(10.0, 0.0) is None

    def test_none_numerator(self):
        assert safe_div(None, 5.0) is None

    def test_none_denominator(self):
        assert safe_div(5.0, None) is None

    def test_decimal_places(self):
        result = safe_div(1.0, 3.0, decimals=2)
        assert result == 0.33


class TestCalculateFinancialRatios:
    SAMPLE_STATEMENTS = {
        2022: {
            "total_assets": 1000.0,
            "current_assets": 600.0,
            "inventories": 100.0,
            "total_liabilities": 400.0,
            "current_liabilities": 300.0,
            "long_term_liabilities": 100.0,
            "equity": 600.0,
            "net_revenue": 800.0,
            "gross_profit": 200.0,
            "net_profit": 80.0,
        },
        2023: {
            "total_assets": 1200.0,
            "current_assets": 700.0,
            "inventories": 120.0,
            "total_liabilities": 480.0,
            "current_liabilities": 350.0,
            "long_term_liabilities": 130.0,
            "equity": 720.0,
            "net_revenue": 960.0,
            "gross_profit": 260.0,
            "net_profit": 100.0,
        },
    }

    def test_returns_all_years(self):
        ratios = calculate_financial_ratios(self.SAMPLE_STATEMENTS)
        assert set(ratios.keys()) == {2022, 2023}

    def test_current_ratio(self):
        ratios = calculate_financial_ratios(self.SAMPLE_STATEMENTS)
        # 600 / 300 = 2.0
        assert ratios[2022]["current_ratio"] == 2.0

    def test_quick_ratio(self):
        ratios = calculate_financial_ratios(self.SAMPLE_STATEMENTS)
        # (600 - 100) / 300 = 1.6667
        assert ratios[2022]["quick_ratio"] == pytest.approx(1.6667, abs=0.001)

    def test_debt_to_equity(self):
        ratios = calculate_financial_ratios(self.SAMPLE_STATEMENTS)
        # 400 / 600 = 0.6667
        assert ratios[2022]["debt_to_equity"] == pytest.approx(0.6667, abs=0.001)

    def test_roe(self):
        ratios = calculate_financial_ratios(self.SAMPLE_STATEMENTS)
        # 80 / 600 * 100 = 13.33%
        assert ratios[2022]["roe"] == pytest.approx(13.33, abs=0.01)

    def test_roa(self):
        ratios = calculate_financial_ratios(self.SAMPLE_STATEMENTS)
        # 80 / 1000 * 100 = 8.0%
        assert ratios[2022]["roa"] == 8.0

    def test_net_profit_margin(self):
        ratios = calculate_financial_ratios(self.SAMPLE_STATEMENTS)
        # 80 / 800 * 100 = 10.0%
        assert ratios[2022]["net_profit_margin"] == 10.0

    def test_no_revenue_growth_for_first_year(self):
        ratios = calculate_financial_ratios(self.SAMPLE_STATEMENTS)
        assert ratios[2022]["revenue_growth_yoy"] is None

    def test_revenue_growth_yoy(self):
        ratios = calculate_financial_ratios(self.SAMPLE_STATEMENTS)
        # (960 - 800) / 800 * 100 = 20.0%
        assert ratios[2023]["revenue_growth_yoy"] == 20.0

    def test_net_profit_growth_yoy(self):
        ratios = calculate_financial_ratios(self.SAMPLE_STATEMENTS)
        # (100 - 80) / 80 * 100 = 25.0%
        assert ratios[2023]["net_profit_growth_yoy"] == 25.0

    def test_missing_data_returns_none(self):
        statements = {
            2022: {"total_assets": 1000.0},  # Missing most fields
        }
        ratios = calculate_financial_ratios(statements)
        assert ratios[2022]["current_ratio"] is None
        assert ratios[2022]["roe"] is None


class TestValidateBalanceSheet:
    def test_balanced_sheet(self):
        statement = {
            "year": 2023,
            "total_assets": 1000.0,
            "total_liabilities": 400.0,
            "equity": 600.0,
        }
        errors = validate_balance_sheet(statement)
        assert errors == []

    def test_imbalanced_sheet(self):
        statement = {
            "year": 2023,
            "total_assets": 1000.0,
            "total_liabilities": 400.0,
            "equity": 500.0,  # 400 + 500 = 900 ≠ 1000
        }
        errors = validate_balance_sheet(statement)
        assert len(errors) == 1
        assert "mismatch" in errors[0]

    def test_within_tolerance(self):
        # Within 2% tolerance
        statement = {
            "year": 2023,
            "total_assets": 1000.0,
            "total_liabilities": 401.0,
            "equity": 600.0,  # 401 + 600 = 1001, diff = 1, within 2% of 1000
        }
        errors = validate_balance_sheet(statement)
        assert errors == []

    def test_missing_data_skips_validation(self):
        statement = {
            "year": 2023,
            "total_assets": None,
            "total_liabilities": 400.0,
            "equity": 600.0,
        }
        errors = validate_balance_sheet(statement)
        assert errors == []
