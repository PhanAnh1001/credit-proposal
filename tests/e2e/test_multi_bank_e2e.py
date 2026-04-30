"""E2E tests for multi-bank pipeline — gated by RUN_E2E=1.

Run with:
    RUN_E2E=1 pytest tests/e2e/test_multi_bank_e2e.py -v
"""
import os
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.getenv("RUN_E2E") != "1",
        reason="E2E requires RUN_E2E=1 and local data fixtures",
    ),
]


def test_full_pipeline_writes_to_bank_scoped_output():
    """Full pipeline writes output files to data/outputs/vpbank/mst/."""
    from src.agents.graph import run_credit_proposal
    from src.config import get_output_dir

    company = "mst"
    bank = "vpbank"
    expected_dir = get_output_dir(bank, company)

    result = run_credit_proposal(
        bank=bank,
        company=company,
        company_name="Công ty Cổ phần Xây dựng MST",
        md_company_info_path=str(
            Path("data/uploads/mst/general-information/md/mst-information.md")
        ),
        pdf_dir_path=str(
            Path("data/uploads/mst/financial-statements/pdf")
        ),
    )

    docx_path = result.get("final_report_docx_path")
    assert docx_path is not None, "final_report_docx_path should be set"
    assert str(expected_dir) in docx_path, (
        f"Expected output under {expected_dir}, got {docx_path}"
    )


def test_unknown_bank_fails_before_graph_runs():
    """run_credit_proposal with unknown bank raises UnsupportedBankError."""
    from src.agents.graph import run_credit_proposal
    from src.banks import UnsupportedBankError

    with pytest.raises(UnsupportedBankError):
        run_credit_proposal(
            bank="hsbc",
            company="mst",
            company_name="Test",
            md_company_info_path="dummy.md",
            pdf_dir_path="dummy/",
        )
