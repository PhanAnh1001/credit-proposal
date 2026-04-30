"""Centralized configuration — all path constants and env-var overrides live here.

Storage layout (local filesystem default):

    data/
    ├── uploads/          company input files (PDF + MD), switchable to S3
    │   └── {company}/
    │       ├── financial-statements/pdf/{year}/
    │       └── general-information/md/
    ├── outputs/          AI agent output files
    │   └── {bank}/
    │       └── {company}/
    ├── cache/
    │   └── ocr/          OCR result cache
    │       └── {company}/{year}/{strategy}/{YYYYMMDD}_vN/
    └── templates/        reference form templates (static assets)
        └── {bank}/
            ├── docx/
            ├── md/
            └── pdf/

To override the data root set DATA_DIR in .env, e.g. DATA_DIR=/mnt/efs/data.
To switch to S3 set STORAGE_BACKEND=s3 (see s3 vars below).
"""
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Project root
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Storage backend
# ---------------------------------------------------------------------------

STORAGE_BACKEND: str = os.getenv("STORAGE_BACKEND", "local")  # "local" | "s3"

# ---------------------------------------------------------------------------
# Local storage paths
# ---------------------------------------------------------------------------

DATA_DIR = PROJECT_ROOT / os.getenv("DATA_DIR", "data")

UPLOADS_DIR   = DATA_DIR / "uploads"       # company input files
OUTPUTS_DIR   = DATA_DIR / "outputs"       # AI agent outputs
OCR_CACHE_DIR = DATA_DIR / "cache" / "ocr" # OCR result cache
TEMPLATES_DIR = DATA_DIR / "templates"     # reference form templates

# ---------------------------------------------------------------------------
# Bank defaults & template basename
# ---------------------------------------------------------------------------

DEFAULT_BANK: str = "vpbank"
FORM_TEMPLATE_BASENAME: str = "giay-de-nghi-vay-von"

# ---------------------------------------------------------------------------
# Bank-scoped path helpers
# ---------------------------------------------------------------------------

def get_bank_templates_dir(bank: str) -> Path:
    """Root template directory for a bank, e.g. data/templates/vpbank/."""
    return TEMPLATES_DIR / bank


def get_form_template_docx(bank: str) -> Path:
    """DOCX form template path for a bank."""
    return get_bank_templates_dir(bank) / "docx" / f"{FORM_TEMPLATE_BASENAME}.docx"


def get_form_template_md(bank: str) -> Path:
    """Markdown form template path for a bank."""
    return get_bank_templates_dir(bank) / "md" / f"{FORM_TEMPLATE_BASENAME}.md"


def get_form_template_pdf(bank: str) -> Path:
    """PDF form template path for a bank."""
    return get_bank_templates_dir(bank) / "pdf" / f"{FORM_TEMPLATE_BASENAME}.pdf"


# ---------------------------------------------------------------------------
# Company path helpers
# ---------------------------------------------------------------------------

def get_company_upload_dir(company: str) -> Path:
    """Root upload directory for a company, e.g. data/uploads/mst/."""
    return UPLOADS_DIR / company


def get_financial_statements_dir(company: str) -> Path:
    """PDF directory for a company's financial statements."""
    return get_company_upload_dir(company) / "financial-statements" / "pdf"


def get_general_info_path(company: str) -> Path:
    """Markdown general-information file for a company."""
    return (
        get_company_upload_dir(company)
        / "general-information"
        / "md"
        / f"{company}-information.md"
    )


def get_output_dir(bank: str, company: str) -> Path:
    """Output directory for a bank+company combination, e.g. data/outputs/vpbank/mst/."""
    return OUTPUTS_DIR / bank / company


# ---------------------------------------------------------------------------
# S3 config (used when STORAGE_BACKEND=s3)
# ---------------------------------------------------------------------------

S3_BUCKET          = os.getenv("S3_BUCKET", "")
S3_REGION          = os.getenv("S3_REGION", "ap-southeast-1")
S3_UPLOADS_PREFIX  = os.getenv("S3_UPLOADS_PREFIX",  "uploads/")
S3_OUTPUTS_PREFIX  = os.getenv("S3_OUTPUTS_PREFIX",  "outputs/")
S3_CACHE_PREFIX    = os.getenv("S3_CACHE_PREFIX",    "cache/ocr/")
