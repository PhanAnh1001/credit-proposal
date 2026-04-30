"""Tests for multi-bank registry contract — CI-safe, no fixtures required."""
import inspect
import pytest

from src.banks import SUPPORTED_BANKS, UnsupportedBankError, get_renderer, validate_bank
from src.config import FORM_TEMPLATE_BASENAME, OUTPUTS_DIR, TEMPLATES_DIR, get_form_template_docx, get_output_dir


class TestBankRegistryContract:

    def test_supported_banks_constant_only_contains_known(self):
        assert SUPPORTED_BANKS == ("vpbank",)

    def test_get_renderer_vpbank_returns_callable_with_expected_signature(self):
        renderer = get_renderer("vpbank")
        sig = inspect.signature(renderer.render)
        params = list(sig.parameters.keys())
        assert "output_path" in params
        assert "company_info" in params
        assert "financial_data" in params

    def test_unknown_bank_raises_unsupported_bank_error(self):
        with pytest.raises(UnsupportedBankError) as exc_info:
            get_renderer("hsbc")
        assert "hsbc" in str(exc_info.value)
        assert "vpbank" in str(exc_info.value)

    def test_validate_bank_normalises_case(self):
        assert validate_bank("VPBank") == "vpbank"
        assert validate_bank("VPBANK") == "vpbank"
        assert validate_bank("vpbank") == "vpbank"

    def test_validate_bank_raises_for_unknown(self):
        with pytest.raises(UnsupportedBankError):
            validate_bank("hsbc")

    def test_template_path_resolves_to_bank_folder(self):
        path = get_form_template_docx("vpbank")
        expected = TEMPLATES_DIR / "vpbank" / "docx" / f"{FORM_TEMPLATE_BASENAME}.docx"
        assert path == expected

    def test_output_dir_is_bank_scoped(self):
        path = get_output_dir("vpbank", "mst")
        expected = OUTPUTS_DIR / "vpbank" / "mst"
        assert path == expected
