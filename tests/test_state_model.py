"""Tests for AgentState and state reducers."""
import pytest
from operator import add
from typing import get_type_hints, get_args


class TestAgentStateStructure:
    def test_state_importable(self):
        from src.models.state import AgentState
        assert AgentState is not None

    def test_required_fields_present(self):
        from src.models.state import AgentState
        hints = AgentState.__annotations__
        required = [
            "company", "company_name", "md_company_info_path", "pdf_dir_path",
            "output_dir", "company_info", "financial_data", "sector_info",
            "section_1_company", "section_2_sector", "section_3_financial",
            "final_report_md", "final_report_docx_path",
            "retry_count", "quality_review_result", "quality_feedback",
            "errors", "current_step", "messages",
        ]
        for field in required:
            assert field in hints, f"Missing field: {field}"

    def test_errors_uses_annotated_reducer(self):
        """errors must use Annotated[list, add] for parallel-safe merge."""
        from src.models.state import AgentState
        import typing
        hints = AgentState.__annotations__
        errors_hint = hints["errors"]
        # Should be Annotated type
        assert hasattr(errors_hint, "__metadata__") or str(errors_hint).startswith("typing.Annotated"), \
            "errors field must use Annotated[list, add] reducer"

    def test_messages_uses_annotated_reducer(self):
        """messages must use Annotated[list, add] for parallel-safe merge."""
        from src.models.state import AgentState
        hints = AgentState.__annotations__
        messages_hint = hints["messages"]
        assert hasattr(messages_hint, "__metadata__") or str(messages_hint).startswith("typing.Annotated"), \
            "messages field must use Annotated[list, add] reducer"


class TestCompanyInfoModel:
    def test_company_info_importable(self):
        from src.models.company import CompanyInfo
        assert CompanyInfo is not None

    def test_company_info_has_required_fields(self):
        from src.models.company import CompanyInfo
        fields = CompanyInfo.model_fields
        required = ["company_name", "tax_code", "address", "shareholders"]
        for f in required:
            assert f in fields, f"Missing field: {f}"

    def test_company_info_instantiation(self):
        from src.models.company import CompanyInfo
        info = CompanyInfo(company_name="Test Corp", tax_code="0123456789")
        assert info.company_name == "Test Corp"
        assert info.tax_code == "0123456789"
        assert info.address is None  # Optional field


class TestFinancialModels:
    def test_financial_statement_importable(self):
        from src.models.financial import FinancialStatement, FinancialRatios, FinancialData
        assert FinancialStatement is not None
        assert FinancialRatios is not None
        assert FinancialData is not None

    def test_financial_statement_defaults(self):
        from src.models.financial import FinancialStatement
        stmt = FinancialStatement(year=2023)
        assert stmt.year == 2023
        assert stmt.total_assets is None
        assert stmt.net_revenue is None
