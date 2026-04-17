"""Centralized configuration — all path constants and env-var overrides live here.

Storage layout (local filesystem default):

    data/
    ├── uploads/          company input files (PDF + MD), switchable to S3
    │   └── {company}/
    │       ├── financial_statements/pdf/{year}/
    │       └── general_information/md/
    ├── outputs/          AI agent output files
    │   └── {company}/
    ├── cache/
    │   └── ocr/          OCR result cache
    │       └── {company}/{year}/{strategy}/{YYYYMMDD}_vN/
    └── templates/        reference form templates (static assets)
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
# Template file paths
# ---------------------------------------------------------------------------

FORM_TEMPLATE_DOCX = TEMPLATES_DIR / "docx" / "giay-de-nghi-vay-von.docx"
FORM_TEMPLATE_MD   = TEMPLATES_DIR / "md"   / "giay-de-nghi-vay-von.md"

# ---------------------------------------------------------------------------
# Company path helpers
# ---------------------------------------------------------------------------

def get_company_upload_dir(company: str) -> Path:
    """Root upload directory for a company, e.g. data/uploads/mst/."""
    return UPLOADS_DIR / company


def get_financial_statements_dir(company: str) -> Path:
    """PDF directory for a company's financial statements."""
    return get_company_upload_dir(company) / "financial_statements" / "pdf"


def get_general_info_path(company: str) -> Path:
    """Markdown general-information file for a company."""
    return (
        get_company_upload_dir(company)
        / "general_information"
        / "md"
        / f"{company}-information.md"
    )


def get_output_dir(company: str) -> Path:
    """Output directory for a company's generated reports."""
    return OUTPUTS_DIR / company


# ---------------------------------------------------------------------------
# S3 config (used when STORAGE_BACKEND=s3)
# ---------------------------------------------------------------------------

S3_BUCKET          = os.getenv("S3_BUCKET", "")
S3_REGION          = os.getenv("S3_REGION", "ap-southeast-1")
S3_UPLOADS_PREFIX  = os.getenv("S3_UPLOADS_PREFIX",  "uploads/")
S3_OUTPUTS_PREFIX  = os.getenv("S3_OUTPUTS_PREFIX",  "outputs/")
S3_CACHE_PREFIX    = os.getenv("S3_CACHE_PREFIX",    "cache/ocr/")
