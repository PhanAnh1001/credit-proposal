import re
import os
import time
import logging
import math
from functools import lru_cache
from langchain_groq import ChatGroq
from dotenv import load_dotenv

load_dotenv(".env")


@lru_cache(maxsize=8)
def get_llm(model: str = "llama-3.3-70b-versatile", temperature: float = 0.0,
            max_tokens: int = 8192) -> ChatGroq:
    """Return a cached Groq ChatLLM instance."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY not set in environment")
    return ChatGroq(
        api_key=api_key,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def get_fast_llm() -> ChatGroq:
    """Lighter model for simple extraction tasks.

    max_tokens capped at 2048 so total request (input + max_tokens) stays
    within the 6 000 TPM limit of llama-3.1-8b-instant on the free tier.
    Suitable for: TOC parsing, simple JSON extraction from small inputs.
    """
    return get_llm(model="llama-3.1-8b-instant", temperature=0.0, max_tokens=2048)


def get_medium_llm() -> ChatGroq:
    """Structured-extraction model for SG1 (company info).

    qwen/qwen3-32b (Qwen, 6K TPM, 1K RPD) — input for company info is
    ~3K tokens, comfortably within 6K TPM. max_tokens=4096 avoids the
    truncated-output issue of gpt-oss-20b (which had 2048 cap).
    Separate vendor/bucket from SG2 (Meta), SG3 (Groq), QR (OpenAI).
    Note: qwen3-32b may emit <think>...</think> blocks — handled by
    strip_llm_json() in company_info.py.
    """
    return get_llm(model="qwen/qwen3-32b", temperature=0.0, max_tokens=4096)


def get_financial_llm() -> ChatGroq:
    """High-TPM model for SG3 financial parsing and narrative generation.

    llama-3.3-70b-versatile (Meta, 12K TPM, 1K RPD, 128K context).
    SG3 is the heaviest consumer: 3 years × (Stage1 ~11K + Stage2 ~7K +
    narrative ~7K) ≈ 75K tokens total → needs highest available text TPM.
    128K context eliminates any 413 risk regardless of input size.
    Separate Meta bucket from vision (llama-4-scout) and fast (llama-3.1-8b).
    """
    return get_llm(model="llama-3.3-70b-versatile", temperature=0.0, max_tokens=4096)


def get_smart_llm() -> ChatGroq:
    """Model for SG2 sector synthesis.

    openai/gpt-oss-120b (OpenAI, 8K TPM, 1K RPD). SG2 is lighter than SG3:
    context[:6000] ~2.3K input + max_tokens=4096 = ~6.4K total/call, safely
    within gpt-oss-120b's context window (120B model has larger window than
    20B). max_tokens capped at 4096 (not default 8192) to prevent potential
    413. Separate OpenAI-120b bucket from judge (OpenAI-20b).
    """
    return get_llm(model="openai/gpt-oss-120b", temperature=0.0, max_tokens=4096)


def get_judge_llm() -> ChatGroq:
    """Independent judge model for LLM-as-Judge quality review.

    openai/gpt-oss-20b (OpenAI, 8K TPM, 1K RPD, ~8K context window).
    max_tokens=2048: input ~1.3K + 2048 output = ~3.3K total — safely
    within ~8K context. Previous truncation was caused by max_tokens=1024
    being too small for the judge's detailed scoring response, not context
    window overflow. Cross-vendor: QR=OpenAI reviews SG1=Qwen, SG3=Meta.
    """
    return get_llm(model="openai/gpt-oss-20b", temperature=0.0, max_tokens=2048)


def get_vision_llm() -> ChatGroq:
    """Vision-capable model for OCR tasks (reading scanned PDFs)."""
    return get_llm(model="meta-llama/llama-4-scout-17b-16e-instruct", temperature=0.0)


_RETRY_AFTER_RE = re.compile(r"try again in ([\d.]+)s", re.IGNORECASE)
_RETRY_FALLBACK_S = 15  # fallback sleep when "try again in Xs" not found in error


def _parse_retry_after(msg: str) -> int:
    """Extract wait time from Groq rate-limit error, round up + 2s buffer."""
    m = _RETRY_AFTER_RE.search(msg)
    if m:
        return math.ceil(float(m.group(1))) + 2
    return _RETRY_FALLBACK_S


def invoke_with_retry(llm: ChatGroq, messages: list, *, retries: int = 2, sleep_s: int | None = None):
    """Invoke an LLM with automatic retry on 429/413 rate-limit errors.

    Parses the "try again in Xs" hint from Groq error responses and sleeps
    exactly that long (+ 2s buffer) instead of a fixed interval — so the
    full original input is retried without needing to trim it.

    Args:
        llm:      ChatGroq instance to call.
        messages: List of LangChain message objects.
        retries:  Number of retry attempts after the first failure (default 2).
        sleep_s:  Override sleep duration in seconds. When None (default) the
                  wait time is parsed from the error message.

    Returns:
        The LLM response object on success.

    Raises:
        The last exception if all attempts are exhausted.
    """
    _log = logging.getLogger("llm.retry")
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            return llm.invoke(messages)
        except Exception as exc:
            msg = str(exc)
            if "429" in msg or "413" in msg or "rate_limit" in msg.lower():
                last_exc = exc
                if attempt < retries:
                    wait = sleep_s if sleep_s is not None else _parse_retry_after(msg)
                    _log.warning(
                        f"Rate limit hit (attempt {attempt + 1}/{retries + 1}) — "
                        f"sleeping {wait}s: {msg[:120]}"
                    )
                    time.sleep(wait)
                    continue
            raise
    raise last_exc  # type: ignore[misc]


_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
# Trailing comma before } or ] — invalid JSON, produced by some models (e.g. gpt-oss-20b)
_TRAILING_COMMA_RE = re.compile(r",\s*([\]}])")
# Python literals that are invalid JSON values (word-boundary safe)
_PYTHON_NONE_RE = re.compile(r"\bNone\b")
_PYTHON_TRUE_RE = re.compile(r"\bTrue\b")
_PYTHON_FALSE_RE = re.compile(r"\bFalse\b")


def strip_llm_json(raw: str) -> str:
    """Strip <think>...</think> blocks, markdown code fences, and trailing commas.

    Handles common LLM JSON output quirks:
    - qwen3-32b: emits <think>...</think> before JSON
    - Any model: wraps JSON in ```json ... ``` fences
    - gpt-oss-20b and others: trailing commas before } or ] (invalid JSON5-style)
    - Some models: Python-style None/True/False instead of null/true/false
    """
    # 1. Remove <think>...</think> blocks (including empty ones)
    raw = _THINK_BLOCK_RE.sub("", raw).strip()

    # 2. Remove markdown code fences (```json ... ``` or ``` ... ```)
    if raw.startswith("```"):
        lines = raw.split("\n")
        start = 1
        end = len(lines)
        if lines[-1].strip() == "```":
            end = len(lines) - 1
        raw = "\n".join(lines[start:end]).strip()

    # 3. Remove trailing commas before closing brackets/braces
    raw = _TRAILING_COMMA_RE.sub(r"\1", raw)

    # 4. Replace Python literals with valid JSON equivalents
    raw = _PYTHON_NONE_RE.sub("null", raw)
    raw = _PYTHON_TRUE_RE.sub("true", raw)
    raw = _PYTHON_FALSE_RE.sub("false", raw)

    return raw
