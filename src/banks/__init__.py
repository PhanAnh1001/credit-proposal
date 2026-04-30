"""Bank registry — single source of truth for supported banks and form renderers."""
import importlib
from typing import Protocol, runtime_checkable

from ..models.company import CompanyInfo
from ..models.financial import FinancialData


SUPPORTED_BANKS: tuple[str, ...] = ("vpbank",)


class UnsupportedBankError(ValueError):
    def __init__(self, bank: str) -> None:
        super().__init__(
            f"Unsupported bank: {bank!r}. Supported: {sorted(SUPPORTED_BANKS)}"
        )
        self.bank = bank


@runtime_checkable
class FormRenderer(Protocol):
    def render(
        self,
        output_path: str,
        company_info: "CompanyInfo | None",
        financial_data: "FinancialData | None",
    ) -> str: ...


def get_renderer(bank: str) -> "FormRenderer":
    """Return the form renderer module for *bank*, importing lazily on first call."""
    if bank not in SUPPORTED_BANKS:
        raise UnsupportedBankError(bank)
    return importlib.import_module(f"src.banks.{bank}.renderer")


def validate_bank(bank: str) -> str:
    """Normalise to lower-case and validate membership; raise UnsupportedBankError if unknown."""
    normalised = bank.lower()
    if normalised not in SUPPORTED_BANKS:
        raise UnsupportedBankError(normalised)
    return normalised
