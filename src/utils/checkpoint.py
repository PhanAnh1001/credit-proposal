"""Lightweight per-node checkpoints.

Saves a JSON snapshot after each node so a failed run can skip completed nodes
on retry without re-running expensive LLM/OCR steps.

Layout:
    data/checkpoints/{run_id}/
        meta.json              # run_id, company, status, started_at
        01_company_info.json   # output of extract_company_info node
        02_financial_data.json # output of analyze_financial node (FinancialData)
        03_quality_review.json # output of quality_review node

Note: rollback logic (load checkpoint on resume) is not implemented for the
interview scope — this demonstrates the checkpoint pattern without the full
distributed state machinery.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..config import DATA_DIR as BASE_DATA_DIR
from .logger import get_logger

logger = get_logger("checkpoint")

_CHECKPOINT_DIR = Path(BASE_DATA_DIR) / "checkpoints"


def _run_dir(run_id: str) -> Path:
    d = _CHECKPOINT_DIR / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_node_checkpoint(run_id: str, node: str, data: dict) -> None:
    """Save a JSON checkpoint for a node's output."""
    try:
        path = _run_dir(run_id) / f"{node}.json"
        payload = {
            "run_id": run_id,
            "node": node,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        logger.debug(f"Checkpoint saved: {path}")
    except Exception as exc:
        logger.warning(f"Checkpoint save failed (non-fatal): {exc}")


def load_node_checkpoint(run_id: str, node: str) -> Optional[dict]:
    """Load a previously saved checkpoint for a node. Returns None if not found."""
    try:
        path = _checkpoint_dir_for(run_id) / f"{node}.json"
        if path.exists():
            payload = json.loads(path.read_text())
            logger.debug(f"Checkpoint loaded: {path}")
            return payload.get("data")
    except Exception as exc:
        logger.warning(f"Checkpoint load failed: {exc}")
    return None


def _checkpoint_dir_for(run_id: str) -> Path:
    return _CHECKPOINT_DIR / run_id


def save_run_meta(run_id: str, company: str, status: str = "running") -> None:
    try:
        path = _run_dir(run_id) / "meta.json"
        meta = {
            "run_id": run_id,
            "company": company,
            "status": status,
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        # Don't overwrite started_at if meta already exists
        if path.exists():
            existing = json.loads(path.read_text())
            meta["started_at"] = existing.get("started_at", meta["started_at"])
        path.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    except Exception as exc:
        logger.warning(f"Meta save failed: {exc}")
