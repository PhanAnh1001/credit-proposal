"""Centralized logging utility for the credit proposal agent.

Features:
- Colored console output by log level
- File logging to logs/ directory with timestamps
- Node execution timer via @timed_node decorator
- LangSmith tracing setup helper
"""

import logging
import sys
import time
import functools
import os
from pathlib import Path
from datetime import datetime
from typing import Callable, Any


# ---------------------------------------------------------------------------
# ANSI color codes
# ---------------------------------------------------------------------------

_COLORS = {
    "DEBUG":    "\033[36m",   # Cyan
    "INFO":     "\033[32m",   # Green
    "WARNING":  "\033[33m",   # Yellow
    "ERROR":    "\033[31m",   # Red
    "CRITICAL": "\033[35m",   # Magenta
    "RESET":    "\033[0m",
    "BOLD":     "\033[1m",
    "DIM":      "\033[2m",
    "WHITE":    "\033[97m",
}

_NODE_ICONS = {
    "extract_company_info": "📋",
    "analyze_sector":       "🔍",
    "analyze_financial":    "📊",
    "assemble_report":      "📄",
    "quality_review":       "✅",
    "pdf_extractor":        "📑",
    "web_search":           "🌐",
    "company_info":         "🏢",
    "ratio_calculator":     "🧮",
    "assembler":            "🔧",
    "graph":                "🕸️",
    "llm":                  "🤖",
}

# ---------------------------------------------------------------------------
# Colored formatter
# ---------------------------------------------------------------------------

class ColoredFormatter(logging.Formatter):
    """Formatter that adds ANSI colors and icons to log records."""

    FMT = "{color}{bold}[{levelname:8s}]{reset} {dim}{asctime}{reset}  {bold_white}{name}{reset}  {msg}"

    def format(self, record: logging.LogRecord) -> str:
        color = _COLORS.get(record.levelname, _COLORS["RESET"])
        reset = _COLORS["RESET"]
        bold  = _COLORS["BOLD"]
        dim   = _COLORS["DIM"]
        bold_white = _COLORS["BOLD"] + _COLORS["WHITE"]

        # Time — short format
        record.asctime = datetime.fromtimestamp(record.created).strftime("%H:%M:%S.%f")[:-3]

        formatted = (
            f"{color}{bold}[{record.levelname:8s}]{reset} "
            f"{dim}{record.asctime}{reset}  "
            f"{bold_white}[{record.name}]{reset}  "
            f"{record.getMessage()}"
        )

        if record.exc_info:
            if not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)
            formatted += f"\n{_COLORS['DIM']}{record.exc_text}{reset}"

        return formatted


class PlainFormatter(logging.Formatter):
    """Plain formatter for file output (no ANSI codes)."""

    def format(self, record: logging.LogRecord) -> str:
        record.asctime = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        msg = f"[{record.levelname:8s}] {record.asctime}  [{record.name}]  {record.getMessage()}"
        if record.exc_info:
            if not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)
            msg += f"\n{record.exc_text}"
        return msg


# ---------------------------------------------------------------------------
# Logger factory
# ---------------------------------------------------------------------------

_configured_loggers: set[str] = set()
_log_dir: Path | None = None


def _get_log_dir() -> Path:
    global _log_dir
    if _log_dir is None:
        # Try to place logs at project root
        project_root = Path(__file__).resolve().parent.parent.parent
        _log_dir = project_root / "logs"
        _log_dir.mkdir(parents=True, exist_ok=True)
    return _log_dir


def get_logger(name: str, level: int = logging.DEBUG) -> logging.Logger:
    """Return a named logger with colored console + rotating file handler.

    Args:
        name: Logger name (used as prefix in log lines).
              Typically the module/node short name, e.g. "subgraph1", "pdf_extractor".
        level: Minimum log level (default DEBUG).

    Returns:
        Configured logging.Logger instance.
    """
    logger = logging.getLogger(name)

    # Avoid adding duplicate handlers on repeated calls
    if name in _configured_loggers:
        return logger

    logger.setLevel(level)
    logger.propagate = False

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(level)
    ch.setFormatter(ColoredFormatter())
    logger.addHandler(ch)

    # File handler (one log file per run session, shared across loggers)
    try:
        log_dir = _get_log_dir()
        session_log = log_dir / f"run_{datetime.now().strftime('%Y%m%d')}.log"
        fh = logging.FileHandler(session_log, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(PlainFormatter())
        logger.addHandler(fh)
    except Exception:
        pass  # Silently skip file logging if directory is not writable

    _configured_loggers.add(name)
    return logger


# ---------------------------------------------------------------------------
# Node timing decorator
# ---------------------------------------------------------------------------

def timed_node(node_name: str | None = None):
    """Decorator that logs entry, exit, timing, and errors for a LangGraph node.

    Usage:
        @timed_node("extract_company_info")
        def extract_company_info_node(state: AgentState) -> dict:
            ...

    The decorator:
    - Logs START with icon when node begins
    - Logs END with elapsed time when node finishes
    - Logs ERROR with exception details if node raises
    - Appends timing info to returned state's `messages` list (if present)
    """
    def decorator(func: Callable) -> Callable:
        _name = node_name or func.__name__
        icon = _NODE_ICONS.get(_name, "⚙️")
        logger = get_logger(_name)

        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> Any:
            logger.info(f"{icon} START  →  {_name}")
            t0 = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                elapsed = time.perf_counter() - t0
                logger.info(f"{icon} END    ←  {_name}  [{elapsed:.2f}s]")

                # Attach timing to state messages if result is a dict
                if isinstance(result, dict):
                    msgs = result.get("messages", [])
                    msgs = list(msgs) if msgs else []
                    msgs.append({"node": _name, "elapsed_s": round(elapsed, 3)})
                    result["messages"] = msgs

                return result
            except Exception as exc:
                elapsed = time.perf_counter() - t0
                logger.error(
                    f"{icon} FAILED ✗  {_name}  [{elapsed:.2f}s]  →  {exc}",
                    exc_info=True,
                )
                raise

        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# LangSmith tracing helper
# ---------------------------------------------------------------------------

def setup_langsmith_tracing(project_name: str = "credit-proposal-agent") -> bool:
    """Enable LangSmith tracing if API key is available.

    Returns True if tracing was enabled, False otherwise.
    """
    logger = get_logger("langsmith")
    api_key = os.getenv("LANGSMITH_API_KEY") or os.getenv("LANGCHAIN_API_KEY")

    if not api_key:
        logger.warning(
            "LangSmith tracing DISABLED — set LANGSMITH_API_KEY in .env to enable"
        )
        return False

    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGCHAIN_PROJECT"] = project_name
    os.environ.setdefault("LANGCHAIN_ENDPOINT", "https://api.smith.langchain.com")
    logger.info(
        f"LangSmith tracing ENABLED  →  project='{project_name}'  "
        f"endpoint={os.environ['LANGCHAIN_ENDPOINT']}  "
        f"key={api_key[:8]}…"
    )
    return True


# ---------------------------------------------------------------------------
# Convenience: log tool calls
# ---------------------------------------------------------------------------

def log_tool_call(logger: logging.Logger, tool_name: str, **kwargs) -> None:
    """Log the invocation of a tool with its key parameters."""
    params = "  ".join(f"{k}={repr(v)[:80]}" for k, v in kwargs.items())
    logger.debug(f"[tool] {tool_name}  {params}")


def log_llm_call(logger: logging.Logger, model: str, prompt_preview: str = "", max_tokens: int | None = None) -> None:
    """Log an LLM call with model name and prompt preview."""
    preview = prompt_preview[:120].replace("\n", " ") if prompt_preview else ""
    extra = f"  max_tokens={max_tokens}" if max_tokens else ""
    logger.debug(f"[llm] model={model}{extra}  prompt='{preview}…'")
