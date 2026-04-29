"""Structured audit trail — append-only JSONL for queryable run history.

Each log entry is one JSON object per line in logs/audit_YYYYMMDD.jsonl.
Queryable with jq or pandas:
    jq 'select(.event=="node_end") | {node, duration_ms}' logs/audit_20260428.jsonl

Key design choices:
- think-blocks are NOT stripped here (unlike LLM response parsing). CoT reasoning
  is preserved for debugging and audit purposes.
- run_id groups all events from one pipeline invocation.
- File is append-only; never overwritten mid-run.
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from .logger import get_logger

logger = get_logger("audit")

_LOG_DIR: Path | None = None


def _get_log_dir() -> Path:
    global _LOG_DIR
    if _LOG_DIR is None:
        project_root = Path(__file__).resolve().parent.parent.parent
        _LOG_DIR = project_root / "logs"
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
    return _LOG_DIR


def _audit_path() -> Path:
    return _get_log_dir() / f"audit_{datetime.now().strftime('%Y%m%d')}.jsonl"


def _write(entry: dict) -> None:
    try:
        line = json.dumps(entry, ensure_ascii=False, default=str)
        with open(_audit_path(), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception as exc:
        logger.warning(f"Audit write failed (non-fatal): {exc}")


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuditLogger:
    """Per-run audit logger. Create one per pipeline invocation via from_run_id()."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id

    @classmethod
    def from_run_id(cls, run_id: str) -> "AuditLogger":
        return cls(run_id)

    def _emit(self, event: str, **kwargs) -> None:
        _write({"ts": _ts(), "run_id": self.run_id, "event": event, **kwargs})

    # ── Node lifecycle ────────────────────────────────────────────────────────

    def node_start(self, node: str, input_summary: dict | None = None) -> float:
        """Log node start. Returns monotonic start time for duration tracking."""
        self._emit("node_start", node=node, input=input_summary or {})
        return time.monotonic()

    def node_end(
        self,
        node: str,
        t0: float,
        output_summary: dict | None = None,
        errors: list[str] | None = None,
    ) -> None:
        duration_ms = round((time.monotonic() - t0) * 1000)
        self._emit(
            "node_end",
            node=node,
            duration_ms=duration_ms,
            output=output_summary or {},
            errors=errors or [],
        )

    def node_error(self, node: str, t0: float, error: str) -> None:
        duration_ms = round((time.monotonic() - t0) * 1000)
        self._emit("node_error", node=node, duration_ms=duration_ms, error=error)

    # ── Tool calls ────────────────────────────────────────────────────────────

    def tool_call(self, tool: str, **kwargs) -> None:
        self._emit("tool_call", tool=tool, **kwargs)

    # ── Circuit breaker events ────────────────────────────────────────────────

    def circuit_breaker_trip(self, node: str, reason: str) -> None:
        self._emit("circuit_breaker_trip", node=node, reason=reason)
        logger.warning(f"[audit] circuit_breaker_trip  node={node}  reason={reason}")

    def circuit_breaker_warn(self, node: str, warnings: list[str]) -> None:
        self._emit("circuit_breaker_warn", node=node, warnings=warnings)

    # ── LLM calls ────────────────────────────────────────────────────────────

    def llm_call(
        self,
        node: str,
        model: str,
        prompt_chars: int,
        max_tokens: int | None = None,
        # think_block is preserved raw — not stripped
        think_block: str | None = None,
    ) -> None:
        entry: dict = {
            "node": node,
            "model": model,
            "prompt_chars": prompt_chars,
        }
        if max_tokens is not None:
            entry["max_tokens"] = max_tokens
        if think_block:
            entry["think_block_chars"] = len(think_block)
            entry["think_block"] = think_block  # preserved for audit
        self._emit("llm_call", **entry)

    # ── Quality review decisions ──────────────────────────────────────────────

    def quality_decision(
        self,
        score: float,
        retry_triggered: bool,
        route: str,
        issues: list[str] | None = None,
    ) -> None:
        self._emit(
            "quality_decision",
            score=score,
            retry_triggered=retry_triggered,
            route=route,
            issues=issues or [],
        )

    # ── Validation results ────────────────────────────────────────────────────

    def validation_result(self, node: str, passed: bool, failures: list[str]) -> None:
        self._emit("validation_result", node=node, passed=passed, failures=failures)

    # ── Pipeline summary ──────────────────────────────────────────────────────

    def pipeline_start(self, company: str, company_name: str) -> None:
        self._emit("pipeline_start", company=company, company_name=company_name)

    def pipeline_end(
        self,
        elapsed_s: float,
        quality_score: float | None,
        retry_count: int,
        errors: list[str],
    ) -> None:
        self._emit(
            "pipeline_end",
            elapsed_s=round(elapsed_s, 2),
            quality_score=quality_score,
            retry_count=retry_count,
            error_count=len(errors),
            errors=errors,
        )


# ── Module-level singleton (lazy per run_id) ─────────────────────────────────

_current: AuditLogger | None = None


def get_audit_logger(run_id: str | None = None) -> AuditLogger:
    """Return the current run's audit logger, creating one if needed."""
    global _current
    if run_id is not None or _current is None:
        _current = AuditLogger(run_id or "unknown")
    return _current
