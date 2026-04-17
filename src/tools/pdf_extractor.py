"""PDF financial statement extractor.

Strategy (in priority order):
1. PyMuPDF text extraction  — fast, text-based PDFs
2. markitdown               — broad format support
3. TOC-guided Vision LLM    — for image-based / scanned PDFs:
   a. OCR first N pages to read the table of contents (mục lục)
   b. LLM parses TOC → find starting page for each financial section
   c. Vision OCR only the targeted page windows (skips irrelevant pages)
4. pdfplumber               — last resort

Each OCR run (strategy 3) is persisted to data/cache/ocr/ so that
repeated runs within the same day re-use the cached text instead of calling
the OCR engine again.  See src/utils/ocr_cache.py for the cache layout.
"""
import base64
import json
import os
import re
import time
from pathlib import Path

from json_repair import repair_json

from langchain_core.messages import HumanMessage, SystemMessage

from ..utils.llm import get_fast_llm, get_financial_llm, get_vision_llm
from ..utils.logger import get_logger
from ..utils.ocr_cache import OcrCache

logger = get_logger("pdf_extractor")

# Shared cache instance (data/cache/ocr/ relative to CWD)
_cache = OcrCache()

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TOC_PAGE_START     = 1     # 0-based index of first TOC page to read (page 2)
_TOC_PAGE_END       = 3     # 0-based index, exclusive (reads pages 2 and 3 only)
_TOC_TEXT_MAX_CHARS = 1000  # Chars of TOC text sent to LLM (keeps token count low)
_SECTION_WINDOW     = 6     # Pages to OCR per financial section after TOC lookup
_MAX_PAGES_FALLBACK = 20    # Max pages to Vision-OCR when TOC cannot be found (hard cap)

_RATE_LIMIT_SLEEP   = 12    # Seconds to sleep on 429/413 before retry
_MAX_RETRIES        = 2     # LLM retry attempts on rate-limit errors

# PDF type detection
_SAMPLE_N_PAGES       = 5    # pages to sample when detecting PDF type
_TEXT_RATIO_THRESHOLD = 0.7  # ≥ this fraction of sampled pages with text → "text" PDF
_MIN_TEXT_CHARS_PAGE  = 100  # printable-char count to classify a page as "has text"

# Image preprocessing (Pillow) — applied before Vision LLM OCR
_PREPROCESS_CONTRAST  = 2.0  # contrast enhancement factor  (1.0 = no change)
_PREPROCESS_SHARPNESS = 2.5  # sharpness enhancement factor (1.0 = no change)

# Section keywords for text-based section extraction (post strategy 1-2)
_SECTION_KEYWORDS = [
    "BẢNG CÂN ĐỐI KẾ TOÁN",
    "KẾT QUẢ HOẠT ĐỘNG KINH DOANH",
    "LƯU CHUYỂN TIỀN TỆ",
    "THUYẾT MINH BÁO CÁO TÀI CHÍNH",
    "Bảng cân đối kế toán",
    "Kết quả hoạt động kinh doanh",
    "Lưu chuyển tiền tệ",
]

_MAX_LINES_PER_SECTION = 300
_FALLBACK_HEAD_LINES   = 500

# ---------------------------------------------------------------------------
# Unit-normalisation & derived-field constants
# ---------------------------------------------------------------------------

# Numeric fields that carry monetary values (triệu đồng).
# Used for unit-anomaly detection and derived-field computation.
_NUMERIC_FIELDS = [
    "total_assets", "current_assets", "cash_and_equivalents",
    "short_term_receivables", "inventories", "non_current_assets", "fixed_assets",
    "total_liabilities", "current_liabilities", "long_term_liabilities",
    "equity", "charter_capital_amount",
    "net_revenue", "gross_profit", "operating_profit",
    "profit_before_tax", "net_profit", "cost_of_goods_sold",
    "selling_expenses", "admin_expenses",
    "operating_cash_flow", "investing_cash_flow", "financing_cash_flow",
]

# If total_assets exceeds this threshold (in triệu đồng), it must be raw VND.
# Vietnam's largest companies top out around 5e8 triệu đồng; 1e10 is safely impossible.
_UNIT_ANOMALY_THRESHOLD = 1e10

# Minimum number of digit characters expected in real TOC text.
# PyMuPDF may return a few chars from image-based PDFs (e.g. digital signatures).
# If the extracted text has fewer digits than this, treat it as not a real TOC.
_TOC_MIN_DIGITS = 5


# ---------------------------------------------------------------------------
# Helpers: env-driven feature flags and limits
# ---------------------------------------------------------------------------

def _is_online_ocr_disabled() -> bool:
    """Return True when OCR_ONLINE_DISABLED=true|1|yes.

    Disables Strategy 4 (Vision LLM OCR via Groq API).
    """
    return os.environ.get("OCR_ONLINE_DISABLED", "").lower() in ("true", "1", "yes")



# ---------------------------------------------------------------------------
# Helper: LLM call with rate-limit retry
# ---------------------------------------------------------------------------

def _invoke_with_retry(llm, messages: list, context: str = "") -> str:
    """Invoke LLM, retry up to _MAX_RETRIES times on 429/413 rate limits.

    Returns the response content string, or "" on unrecoverable failure.
    """
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            return llm.invoke(messages).content.strip()
        except Exception as e:
            err = str(e)
            is_rate_limit = any(x in err for x in ("rate_limit_exceeded", "429", "413"))
            if is_rate_limit and attempt < _MAX_RETRIES:
                logger.warning(
                    f"{context} rate limit (attempt {attempt}/{_MAX_RETRIES}), "
                    f"sleeping {_RATE_LIMIT_SLEEP}s…"
                )
                time.sleep(_RATE_LIMIT_SLEEP)
            else:
                logger.warning(f"{context} LLM call failed: {e}")
                return ""
    return ""


# ---------------------------------------------------------------------------
# PDF type detection
# ---------------------------------------------------------------------------

def _detect_pdf_type(pdf_path: str) -> str:
    """Detect whether a PDF is text-based, image-based, or mixed.

    Samples up to _SAMPLE_N_PAGES pages evenly distributed across the document.
    For each sampled page the text layer is extracted with PyMuPDF and printable
    characters are counted.  A page is labelled "has text" when it yields
    ≥ _MIN_TEXT_CHARS_PAGE printable (non-space) chars.

    Returns:
        "text"    — ≥ _TEXT_RATIO_THRESHOLD of sampled pages have substantial text
        "image"   — ≤ (1 − _TEXT_RATIO_THRESHOLD) of sampled pages have text
        "mixed"   — somewhere between the two thresholds
        "unknown" — file cannot be opened or zero pages sampled
    """
    try:
        import fitz
        doc         = fitz.open(pdf_path)
        total_pages = len(doc)
        if total_pages == 0:
            return "unknown"

        # Evenly-distributed sample indices; always include first and last page
        step    = max(1, total_pages // _SAMPLE_N_PAGES)
        indices = list(range(0, total_pages, step))[:_SAMPLE_N_PAGES]
        if (total_pages - 1) not in indices:
            indices.append(total_pages - 1)

        text_count = 0
        for idx in indices:
            raw       = doc[idx].get_text()
            printable = sum(1 for c in raw if c.isprintable() and not c.isspace())
            if printable >= _MIN_TEXT_CHARS_PAGE:
                text_count += 1

        ratio = text_count / len(indices)
        if ratio >= _TEXT_RATIO_THRESHOLD:
            pdf_type = "text"
        elif ratio <= 1.0 - _TEXT_RATIO_THRESHOLD:
            pdf_type = "image"
        else:
            pdf_type = "mixed"

        logger.debug(
            f"PDF type: {pdf_type} "
            f"({text_count}/{len(indices)} text pages, ratio={ratio:.2f})"
        )
        return pdf_type

    except Exception as e:
        logger.debug(f"PDF type detection failed: {e}")
        return "unknown"


# ---------------------------------------------------------------------------
# Image preprocessing for Vision OCR
# ---------------------------------------------------------------------------

def _preprocess_page_image(img_b64: str) -> str:
    """Enhance a base64-encoded page image to improve Vision LLM OCR accuracy.

    Transformations applied (in order):
    1. Grayscale conversion  — strips colour noise irrelevant to text recognition
    2. Auto-contrast         — stretches the histogram to use the full 0-255 range
    3. Contrast boost        — makes text stand out from the background
    4. Sharpness boost       — recovers edge detail lost in blurry or low-DPI scans
    5. Unsharp mask          — additional high-frequency edge sharpening for fine text

    The processed image is re-encoded as a PNG and returned as a base64 string.
    On any failure the *original* base64 string is returned unchanged so that
    preprocessing never blocks the OCR pipeline.
    """
    try:
        import base64 as _b64
        from io import BytesIO

        from PIL import Image, ImageEnhance, ImageFilter, ImageOps

        img_bytes = _b64.b64decode(img_b64)
        img       = Image.open(BytesIO(img_bytes)).convert("L")  # grayscale

        # Auto-contrast: clips top/bottom 2% of histogram before stretching
        img = ImageOps.autocontrast(img, cutoff=2)

        # Contrast enhancement
        img = ImageEnhance.Contrast(img).enhance(_PREPROCESS_CONTRAST)

        # Sharpness enhancement
        img = ImageEnhance.Sharpness(img).enhance(_PREPROCESS_SHARPNESS)

        # Unsharp mask: radius=1.5 px, percent=150, threshold=3
        img = img.filter(ImageFilter.UnsharpMask(radius=1.5, percent=150, threshold=3))

        # Convert back to RGB so the vision model sees a standard 3-channel image
        img = img.convert("RGB")

        buf = BytesIO()
        img.save(buf, format="PNG", optimize=True)
        result = _b64.b64encode(buf.getvalue()).decode()
        logger.debug(
            f"Image preprocessed: {len(img_b64)} → {len(result)} chars "
            f"({len(result) * 100 // len(img_b64)}% of original size)"
        )
        return result

    except Exception as e:
        logger.debug(f"Image preprocessing failed — returning original: {e}")
        return img_b64


# ---------------------------------------------------------------------------
# Strategy 1: PyMuPDF text
# ---------------------------------------------------------------------------

def _try_pymupdf_text(pdf_path: str) -> str:
    try:
        import fitz
        doc   = fitz.open(pdf_path)
        parts = [page.get_text() for page in doc]
        return "\n".join(p for p in parts if p and len(p.strip()) > 30)
    except Exception as e:
        logger.debug(f"PyMuPDF text extraction failed: {e}")
        return ""


# ---------------------------------------------------------------------------
# Strategy 2: markitdown
# ---------------------------------------------------------------------------

def _try_markitdown(pdf_path: str) -> str:
    try:
        from markitdown import MarkItDown
        return MarkItDown().convert(pdf_path).text_content or ""
    except Exception as e:
        logger.debug(f"markitdown failed: {e}")
        return ""


# ---------------------------------------------------------------------------
# Strategy 3 helpers: page-level Vision OCR
# ---------------------------------------------------------------------------

def _page_to_base64(
    pdf_path: str,
    page_idx: int,
    zoom: float = 2.0,
    preprocess: bool = True,
) -> str | None:
    """Render a PDF page to a base64-encoded PNG.

    Args:
        zoom:       Render scale factor.  2.0 → ~144 DPI (better OCR than 1.5×).
        preprocess: When True (default), enhance the image via
                    _preprocess_page_image() before returning, which improves
                    Vision LLM OCR accuracy on blurry or low-quality scans.
    """
    try:
        import fitz
        doc = fitz.open(pdf_path)
        if not (0 <= page_idx < len(doc)):
            return None
        pix     = doc[page_idx].get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        img_b64 = base64.b64encode(pix.tobytes("png")).decode()
        if preprocess:
            img_b64 = _preprocess_page_image(img_b64)
        return img_b64
    except Exception as e:
        logger.debug(f"page_to_base64 failed page {page_idx}: {e}")
        return None


def _ocr_page_list(
    pdf_path: str,
    page_indices: list[int],
    company: str,
    year: int,
    run_dir: Path,
    label: str = "",
) -> str:
    """Vision-OCR a specific list of pages (0-based) and save each page to cache."""
    try:
        import fitz
        total = len(fitz.open(pdf_path))
    except Exception:
        total = 999

    llm   = get_vision_llm()
    parts: list[str] = []
    tag   = f"{label} " if label else ""

    for idx in page_indices:
        img_b64 = _page_to_base64(pdf_path, idx)
        if not img_b64:
            continue

        logger.debug(f"Vision OCR {tag}page {idx + 1}/{total}")
        content = _invoke_with_retry(
            llm,
            [HumanMessage(content=[
                {
                    "type": "text",
                    "text": (
                        "Đây là một trang báo cáo tài chính Việt Nam được scan.\n"
                        "Nhiệm vụ: Chép lại CHÍNH XÁC những gì in trên trang.\n"
                        "- Bảng số liệu: dùng Markdown table (| cột1 | cột2 |) "
                        "để giữ cấu trúc hàng/cột, mã VAS, "
                        "số tiền (đơn vị đồng hoặc triệu đồng).\n"
                        "- Nếu trang trống hoặc chỉ có chữ ký/con dấu: "
                        "chỉ chép những gì thực sự có, không thêm gì.\n"
                        "TUYỆT ĐỐI không tự bịa số liệu hay nội dung không có trong ảnh.\n"
                        "Chỉ trả về nội dung văn bản, không giải thích thêm."
                    ),
                },
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
            ])],
            context=f"Vision OCR page {idx + 1}",
        )

        # Save this page to cache regardless of content (empty = page unreadable)
        _cache.save_page(run_dir, page_num=idx + 1, text=content)

        if content:
            parts.append(f"=== Trang {idx + 1} ===\n{content}")

    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Strategy 4: TOC extraction and page-range detection
# ---------------------------------------------------------------------------

def _read_toc_text(pdf_path: str) -> str:
    """Return text from TOC pages (_TOC_PAGE_START.._TOC_PAGE_END, 0-based exclusive).

    Reads pages 2 and 3 (indices 1–2) by default.  Tries PyMuPDF first.
    A quality check ensures the result has enough digit characters to plausibly
    contain page-number references — scanned PDFs often return only a digital-
    signature fragment which is useless as a TOC.

    Falls back to Vision OCR when PyMuPDF text fails the quality check, unless
    online OCR is disabled (OCR_ONLINE_DISABLED=true).
    """
    try:
        import fitz
        doc      = fitz.open(pdf_path)
        toc_range = range(_TOC_PAGE_START, min(_TOC_PAGE_END, len(doc)))
        parts = []
        for i in toc_range:
            text = doc[i].get_text().strip()
            if text and len(text) > 30:
                parts.append(f"=== Trang {i + 1} ===\n{text}")
        if parts:
            combined    = "\n\n".join(parts)
            digit_count = sum(c.isdigit() for c in combined)
            if digit_count >= _TOC_MIN_DIGITS:
                logger.debug(
                    f"TOC text via PyMuPDF "
                    f"(pages {_TOC_PAGE_START + 1}–{min(_TOC_PAGE_END, len(doc))}, "
                    f"{digit_count} digits)"
                )
                return combined
            logger.debug(
                f"PyMuPDF TOC has only {digit_count} digits — "
                "likely scanned, trying Vision OCR for TOC"
            )
    except Exception as e:
        logger.debug(f"PyMuPDF TOC read failed: {e}")

    if _is_online_ocr_disabled():
        logger.info("OCR_ONLINE_DISABLED=true — skipping Vision OCR for TOC")
        return ""

    # Scanned PDF: Vision-OCR the TOC pages to read the table of contents
    logger.debug(
        f"Reading TOC via Vision OCR for pages "
        f"{_TOC_PAGE_START + 1}–{_TOC_PAGE_END}"
    )
    llm   = get_vision_llm()
    parts = []
    for idx in range(_TOC_PAGE_START, _TOC_PAGE_END):
        img_b64 = _page_to_base64(pdf_path, idx)
        if not img_b64:
            continue
        content = _invoke_with_retry(
            llm,
            [HumanMessage(content=[
                {
                    "type": "text",
                    "text": (
                        "Trang này có thể là MỤC LỤC của báo cáo tài chính Việt Nam.\n"
                        "Nhiệm vụ: Chép lại CHÍNH XÁC những gì in trên trang.\n"
                        "Nếu là trang MỤC LỤC:\n"
                        "  - Chép từng dòng theo đúng định dạng: 'Tên mục    Số trang'\n"
                        "  - Số trang nằm ở cột PHẢI (ví dụ: 5 - 6, 7, 8 - 9) — "
                        "bắt buộc phải chép số này.\n"
                        "Nếu là trang bìa hoặc trang khác: chép đúng những gì có.\n"
                        "TUYỆT ĐỐI không thêm nội dung không có trong ảnh. "
                        "Chỉ trả về văn bản thuần."
                    ),
                },
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
            ])],
            context=f"TOC OCR page {idx + 1}",
        )
        if content:
            parts.append(f"=== Trang {idx + 1} ===\n{content}")
    return "\n\n".join(parts)


# Regex patterns for Vietnamese BCTC table-of-contents section lines.
# Applied to lowercased text.  Each pattern captures the trailing page number.
# [^0-9]{0,80} allows for leader dots, spaces, or short section subtitles.
_TOC_REGEX: dict[str, re.Pattern] = {
    "balance_sheet":    re.compile(r"bảng\s+cân\s+đối\s+kế\s+toán[^0-9]{0,80}(\d{1,3})"),
    "income_statement": re.compile(r"kết\s+quả\s+hoạt\s+động\s+kinh\s+doanh[^0-9]{0,80}(\d{1,3})"),
    "cash_flow":        re.compile(r"lưu\s+chuyển\s+tiền[^0-9]{0,80}(\d{1,3})"),
    "notes":            re.compile(r"thuyết\s+minh[^0-9]{0,80}(\d{1,3})"),
}

# Keyword-only patterns (no page number required).
# Used to detect TOCs that list section titles without page numbers — a format
# seen in some Vietnamese BCTCs (e.g. MST 2024).  When keywords are present
# but no page numbers follow them, the LLM TOC parser cannot help either, so
# we skip that API call and go straight to the offset fallback scan.
_TOC_KEYWORD_ONLY: dict[str, re.Pattern] = {
    "balance_sheet":    re.compile(r"bảng\s+cân\s+đối\s+kế\s+toán"),
    "income_statement": re.compile(r"kết\s+quả\s+hoạt\s+động\s+kinh\s+doanh"),
    "cash_flow":        re.compile(r"lưu\s+chuyển\s+tiền"),
    "notes":            re.compile(r"thuyết\s+minh"),
}


def _toc_has_section_keywords(toc_text: str) -> bool:
    """Return True when TOC text contains at least one BCTC section keyword.

    Uses keyword-only patterns (no page number required) so this returns True
    for both "TOC with page numbers" and "TOC without page numbers" formats.
    """
    lower = toc_text.lower()
    return any(pat.search(lower) for pat in _TOC_KEYWORD_ONLY.values())


def _parse_toc_with_regex(toc_text: str, total_pages: int) -> dict[str, int] | None:
    """Try to extract section page numbers from TOC text using regex (no LLM call).

    Operates on lowercased text. Converts printed page numbers to 0-based PDF
    indices assuming first printed page = 1 (true for most Vietnamese BCTCs).

    Returns {section: 0-based pdf index} when at least ``balance_sheet`` or
    ``income_statement`` is found; otherwise returns None so the caller can
    fall back to the LLM-based parser.
    """
    lower = toc_text.lower()
    result: dict[str, int] = {}

    for section, pat in _TOC_REGEX.items():
        m = pat.search(lower)
        if m:
            printed_page = int(m.group(1))
            pdf_idx = max(0, min(printed_page - 1, total_pages - 1))
            result[section] = pdf_idx

    # Require at least one of the two main financial statements
    if "balance_sheet" in result or "income_statement" in result:
        logger.info(
            "TOC parsed via regex — sections: "
            + ", ".join(f"{k}={v + 1}" for k, v in result.items())
        )
        return result

    logger.debug(f"Regex TOC found {len(result)} section(s) — falling back to LLM")
    return None


def _parse_toc_with_llm(toc_text: str, total_pages: int) -> dict[str, int] | None:
    """Ask LLM to parse the TOC and return 0-based PDF page indices per section.

    Uses fast LLM — TOC parsing is simple extraction, does not need 70B model.
    """
    llm = get_fast_llm()

    system_prompt = (
        f"Bạn đọc mục lục báo cáo tài chính Việt Nam. Tổng trang PDF: {total_pages}.\n"
        "Trả về JSON (null nếu không tìm thấy):\n"
        '{"balance_sheet_printed_page": <int|null>, '
        '"income_statement_printed_page": <int|null>, '
        '"cash_flow_printed_page": <int|null>, '
        '"notes_printed_page": <int|null>, '
        '"first_printed_page": <số trang in nhỏ nhất trong mục lục>}\n'
        "Trả về JSON thuần túy, không markdown."
    )

    raw = _invoke_with_retry(
        llm,
        [SystemMessage(content=system_prompt),
         HumanMessage(content=f"Mục lục:\n\n{toc_text[:_TOC_TEXT_MAX_CHARS]}")],
        context="TOC parsing",
    )
    if not raw:
        return None

    raw = _strip_fences(raw)

    try:
        parsed: dict = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.warning(f"TOC JSON decode failed: {e}  raw={raw[:200]!r}")
        return None

    first_printed = parsed.get("first_printed_page") or 1
    offset        = first_printed - 1

    mapping = {
        "balance_sheet":    "balance_sheet_printed_page",
        "income_statement": "income_statement_printed_page",
        "cash_flow":        "cash_flow_printed_page",
        "notes":            "notes_printed_page",
    }
    result: dict[str, int] = {}
    for key, field in mapping.items():
        printed = parsed.get(field)
        if printed and isinstance(printed, int):
            pdf_idx = max(0, min(printed - offset - 1, total_pages - 1))
            result[key] = pdf_idx

    if not result:
        logger.warning("TOC parsed but no page numbers found")
        return None

    logger.info(
        "TOC parsed — sections at PDF pages (0-based): "
        + ", ".join(f"{k}={v + 1}" for k, v in result.items())
    )
    return result


def _get_target_page_indices(
    toc_text: str,
    total_pages: int,
) -> list[int] | None:
    """Given already-read TOC text, return sorted 0-based page indices covering
    all financial sections.  Returns None if TOC cannot be parsed.

    Parse order: regex first (free, instant) → LLM fallback (API call).
    """
    if not toc_text.strip():
        logger.warning("Could not read TOC — will scan all pages")
        return None

    # Try regex first — no API call, works for standard Vietnamese BCTC layout
    section_starts = _parse_toc_with_regex(toc_text, total_pages)
    if section_starts is None:
        if _toc_has_section_keywords(toc_text):
            # Section keywords are present but no page numbers follow them.
            # This is a known TOC format (e.g. MST 2024) where titles are listed
            # without page references.  The LLM TOC parser cannot infer page numbers
            # from absent data — skip the API call and let the caller use an offset
            # fallback scan (starting after the known TOC pages).
            logger.info(
                "TOC has section keywords but no page numbers — "
                "skipping LLM parse, using offset fallback scan"
            )
            return None
        # No recognizable section keywords — unusual layout or heavy OCR noise.
        # Try LLM as a last resort before giving up on TOC-guided scanning.
        logger.info("Regex TOC parse failed — falling back to LLM")
        section_starts = _parse_toc_with_llm(toc_text, total_pages)
    if not section_starts:
        return None

    page_set: set[int] = set()
    for section, start_idx in section_starts.items():
        end = min(start_idx + _SECTION_WINDOW, total_pages)
        page_set.update(range(start_idx, end))
        logger.debug(f"  {section}: pages {start_idx + 1}–{end}")

    target = sorted(page_set)
    logger.info(
        f"TOC-guided OCR: {len(target)} pages targeted "
        f"(out of {total_pages} total) — pages: {[p + 1 for p in target]}"
    )
    return target


# ---------------------------------------------------------------------------
# Strategy 3 (full): TOC-guided Vision OCR with caching
# ---------------------------------------------------------------------------

def _ocr_pdf_with_vision_llm(pdf_path: str, company: str, year: int) -> str:
    """OCR a scanned PDF via Vision LLM, guided by TOC, with result caching."""

    # --- cache check ---
    cached = _cache.load_latest_full_text(company, year, "vision_llm", today_only=False)
    if cached:
        return cached

    try:
        import fitz
        total_pages = len(fitz.open(pdf_path))
    except Exception as e:
        logger.error(f"Cannot open PDF: {e}")
        return ""

    # Read TOC and save to cache for debugging
    logger.info("Reading table of contents from first pages...")
    toc_text = _read_toc_text(pdf_path)
    if toc_text.strip():
        _cache.save_toc_text(company, year, toc_text)

    target = _get_target_page_indices(toc_text, total_pages)

    run_dir = _cache.new_run(company, year, "vision_llm", pdf_path)
    t0      = time.perf_counter()

    if target is not None:
        logger.info(f"Strategy 3 (TOC-guided Vision OCR): OCR-ing {len(target)} pages")
        text = _ocr_page_list(pdf_path, target, company, year, run_dir, label="financial")
        ocr_pages = [p + 1 for p in target]
    else:
        # If the TOC listed section keywords (without page numbers), the first
        # _TOC_PAGE_END pages are known cover/TOC pages — skip them.
        # Otherwise (no TOC at all), scan from page 0 to not miss anything.
        toc_offset   = _TOC_PAGE_END if _toc_has_section_keywords(toc_text) else 0
        fallback_end = min(total_pages, toc_offset + _MAX_PAGES_FALLBACK)
        fallback     = list(range(toc_offset, fallback_end))
        logger.info(
            f"Strategy 3 (fallback Vision OCR): scanning pages "
            f"{toc_offset + 1}–{fallback_end} "
            f"({'skipping TOC pages' if toc_offset else 'from start'})"
        )
        text      = _ocr_page_list(pdf_path, fallback, company, year, run_dir, label="fallback")
        ocr_pages = [p + 1 for p in fallback]

    elapsed = time.perf_counter() - t0
    _cache.save_full_text(run_dir, text)
    _cache.finish_run(run_dir, elapsed_s=elapsed, total_pages=total_pages, ocr_pages=ocr_pages)

    return text


# ---------------------------------------------------------------------------
# Strategy 4: pdfplumber
# ---------------------------------------------------------------------------

def _try_pdfplumber(pdf_path: str) -> str:
    try:
        import pdfplumber
        parts: list[str] = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    parts.append(text)
                for table in page.extract_tables():
                    for row in table:
                        if row:
                            parts.append(" | ".join(str(c) if c else "" for c in row))
        return "\n".join(parts)
    except Exception as e:
        logger.debug(f"pdfplumber failed: {e}")
        return ""


# ---------------------------------------------------------------------------
# Dispatcher: try all strategies in order
# ---------------------------------------------------------------------------

def _convert_pdf_to_text(pdf_path: str, company: str, year: int) -> str:
    """Try all strategies in priority order and return the first good result.

    PDF type detection runs first so that image-based PDFs skip the text-
    extraction strategies (Strategy 1 and 2) and go directly to Vision OCR,
    saving several seconds per file.
    """
    pdf_type = _detect_pdf_type(pdf_path)
    logger.info(f"PDF type: {pdf_type} — {Path(pdf_path).name}")

    if pdf_type != "image":
        text = _try_pymupdf_text(pdf_path)
        if len(text.strip()) > 200:
            logger.info(f"Strategy 1 (PyMuPDF) succeeded — {len(text)} chars")
            return text

        text = _try_markitdown(pdf_path)
        if len(text.strip()) > 200:
            logger.info(f"Strategy 2 (markitdown) succeeded — {len(text)} chars")
            return text
    else:
        logger.info("Skipping Strategy 1/2 (text extraction) — image-based PDF detected")

    if _is_online_ocr_disabled():
        logger.info("Strategy 3 (Vision OCR) skipped — OCR_ONLINE_DISABLED=true")
    else:
        reason = "image-based PDF" if pdf_type == "image" else "text strategies failed"
        logger.info(f"Starting Strategy 3 (TOC-guided Vision OCR) — {reason}")
        text = _ocr_pdf_with_vision_llm(pdf_path, company, year)
        if len(text.strip()) > 200:
            logger.info(f"Strategy 3 (Vision OCR) succeeded — {len(text)} chars")
            return text

    text = _try_pdfplumber(pdf_path)
    if text.strip():
        logger.info(f"Strategy 4 (pdfplumber) succeeded — {len(text)} chars")
        return text

    logger.warning(f"All strategies failed for {pdf_path}")
    return ""


# ---------------------------------------------------------------------------
# Text-based section extractor
# ---------------------------------------------------------------------------

def _extract_financial_sections(text: str) -> str:
    """Return only the financial-statement sections from extracted text."""
    lines         = text.split("\n")
    relevant: list[str] = []
    in_section    = False
    section_count = 0

    for line in lines:
        if any(kw.lower() in line.lower() for kw in _SECTION_KEYWORDS):
            in_section    = True
            section_count = 0
            relevant.append(line)
        elif in_section:
            relevant.append(line)
            section_count += 1
            if section_count >= _MAX_LINES_PER_SECTION:
                in_section = False

    return "\n".join(relevant) if relevant else "\n".join(lines[:_FALLBACK_HEAD_LINES])


# ---------------------------------------------------------------------------
# Post-LLM normalisation helpers (pure Python, no LLM)
# ---------------------------------------------------------------------------

def _normalize_units(d: dict, year: int) -> dict:
    """Auto-detect raw-VND values and convert to triệu đồng in-place.

    The LLM is asked to divide by 1,000,000 but sometimes returns raw VND.
    Detection: if ``total_assets`` exceeds _UNIT_ANOMALY_THRESHOLD (10 billion
    triệu = physically impossible), every numeric field is divided by 1,000,000.
    """
    ta = d.get("total_assets")
    if ta is not None and abs(ta) > _UNIT_ANOMALY_THRESHOLD:
        logger.info(
            f"year={year}: total_assets={ta:.3e} looks like raw VND — "
            f"dividing all numeric fields by 1,000,000"
        )
        for k in _NUMERIC_FIELDS:
            if d.get(k) is not None:
                d[k] = round(d[k] / 1_000_000, 3)
    return d


def _fill_derived_fields(d: dict, year: int) -> dict:
    """Fill None fields using accounting identities (pure Python, deterministic).

    Balance sheet identity:  Assets = Liabilities + Equity
      → equity              = total_assets − total_liabilities
      → total_liabilities   = total_assets − equity
      → non_current_assets  = total_assets − current_assets
      → long_term_liabilities = total_liabilities − current_liabilities

    Income statement identity:
      → gross_profit = net_revenue − cost_of_goods_sold
    """
    ta  = d.get("total_assets")
    tl  = d.get("total_liabilities")
    eq  = d.get("equity")
    ca  = d.get("current_assets")
    cl  = d.get("current_liabilities")
    nr  = d.get("net_revenue")
    cogs = d.get("cost_of_goods_sold")

    derived: list[str] = []

    if eq is None and ta is not None and tl is not None:
        d["equity"] = round(ta - tl, 3)
        derived.append(f"equity={d['equity']:,.0f}")

    if tl is None and ta is not None and eq is not None:
        d["total_liabilities"] = round(ta - eq, 3)
        derived.append(f"total_liabilities={d['total_liabilities']:,.0f}")

    if d.get("non_current_assets") is None and ta is not None and ca is not None:
        d["non_current_assets"] = round(ta - ca, 3)
        derived.append(f"non_current_assets={d['non_current_assets']:,.0f}")

    if d.get("long_term_liabilities") is None and d.get("total_liabilities") is not None and cl is not None:
        d["long_term_liabilities"] = round(d["total_liabilities"] - cl, 3)
        derived.append(f"long_term_liabilities={d['long_term_liabilities']:,.0f}")

    if d.get("gross_profit") is None and nr is not None and cogs is not None:
        d["gross_profit"] = round(nr - cogs, 3)
        derived.append(f"gross_profit={d['gross_profit']:,.0f}")

    if derived:
        logger.info(f"year={year}: derived fields — {', '.join(derived)}")

    return d


# ---------------------------------------------------------------------------
# LLM-based financial data parsing
# ---------------------------------------------------------------------------

_FINANCIAL_JSON_SCHEMA = """\
{
  "year": <năm (int)>,
  "total_assets": <Tổng tài sản>,
  "current_assets": <Tài sản ngắn hạn>,
  "cash_and_equivalents": <Tiền và tương đương tiền>,
  "short_term_receivables": <Phải thu ngắn hạn>,
  "inventories": <Hàng tồn kho>,
  "non_current_assets": <Tài sản dài hạn>,
  "fixed_assets": <Tài sản cố định>,
  "total_liabilities": <Tổng nợ phải trả>,
  "current_liabilities": <Nợ ngắn hạn>,
  "long_term_liabilities": <Nợ dài hạn>,
  "equity": <Vốn chủ sở hữu>,
  "charter_capital_amount": <Vốn điều lệ (số tiền)>,
  "net_revenue": <Doanh thu thuần>,
  "gross_profit": <Lợi nhuận gộp>,
  "operating_profit": <Lợi nhuận từ HĐKD>,
  "profit_before_tax": <Lợi nhuận trước thuế>,
  "net_profit": <Lợi nhuận sau thuế>,
  "cost_of_goods_sold": <Giá vốn hàng bán>,
  "selling_expenses": <Chi phí bán hàng>,
  "admin_expenses": <Chi phí QLDN>,
  "operating_cash_flow": <Lưu chuyển tiền từ HĐKD>,
  "investing_cash_flow": <Lưu chuyển tiền từ HĐ đầu tư>,
  "financing_cash_flow": <Lưu chuyển tiền từ HĐ tài chính>
}"""


def _strip_fences(raw: str) -> str:
    """Strip <think> blocks, markdown code fences, and CoT prose from an LLM response.

    llama-4-scout follows CoT instructions literally and writes out reasoning
    steps before the JSON object. After stripping fences/think-blocks, find
    the first '{' so that preamble prose is discarded.
    """
    from ..utils.llm import strip_llm_json
    raw = strip_llm_json(raw)
    # If LLM wrote CoT prose before the JSON, skip to first '{'
    brace_pos = raw.find("{")
    if brace_pos > 0:
        raw = raw[brace_pos:]
    return raw


def _parse_financial_data_with_llm(text: str, year: int) -> dict:
    """Parse extracted text → structured financial dict via two-stage LLM calls.

    Two stages to stay within Groq free-tier 12,000 TPM per-request limit:
      Stage 1 (≤8000 chars): balance sheet fields from CĐKT section
      Stage 2 (≤5000 chars): income statement + cash flow fields from KQKD/LCTT section

    Raises ValueError if stage 1 fails entirely (stage 2 failure is warned but not fatal).
    """
    llm = get_financial_llm()  # llama-3.3-70b: 12K TPM, 128K context

    # ------------------------------------------------------------------ #
    # Locate KQKD section boundary                                        #
    # ------------------------------------------------------------------ #
    _KQKD_MARKERS = [
        "KẾT QUẢ HOẠT ĐỘNG KINH DOANH",
        "Kết quả hoạt động kinh doanh",
        "KẾT QUẢ KINH DOANH",
    ]
    kqkd_pos: int | None = None
    for marker in _KQKD_MARKERS:
        idx = text.find(marker)
        if idx != -1:
            kqkd_pos = idx
            logger.debug(f"year={year} KQKD section found at char {idx} (marker={marker!r})")
            break
    if kqkd_pos is None:
        logger.warning(f"year={year} KQKD section not found — stage 2 will be skipped")

    # ------------------------------------------------------------------ #
    # Locate CĐKT section start (case-sensitive — avoids audit-report    #
    # mentions which are in mixed case)                                  #
    # ------------------------------------------------------------------ #
    _CDKT_START_MARKERS = [
        "BẢNG CÂN ĐỐI KẾ TOÁN",  # exact uppercase section header
        "TÀI SẢN NGẮN HẠN",       # first table category (fallback)
    ]
    cdkt_start: int = 0
    for _marker in _CDKT_START_MARKERS:
        _idx = text.find(_marker)
        if _idx != -1:
            cdkt_start = _idx
            logger.debug(f"year={year} CĐKT start at char {_idx} (marker={_marker!r})")
            break

    # ------------------------------------------------------------------ #
    # Stage 1 — balance sheet (CĐKT)                                      #
    # Send from actual table start to KQKD boundary (≤11 000 chars)      #
    # ------------------------------------------------------------------ #
    _CDKT_MAX = 11000
    cdkt_end  = min(cdkt_start + _CDKT_MAX, kqkd_pos) if kqkd_pos is not None else cdkt_start + _CDKT_MAX
    cdkt_text = text[cdkt_start:cdkt_end]

    # Chain-of-thought: ask LLM to reason step-by-step, reducing hallucination
    cdkt_system = (
        f"Phân tích và trích xuất số liệu BẢNG CÂN ĐỐI KẾ TOÁN năm {year}.\n\n"
        "Thực hiện TỪNG BƯỚC:\n"
        f"Bước 1: Tìm tiêu đề 'BẢNG CÂN ĐỐI KẾ TOÁN' và xác định cột năm {year} "
        "('Số cuối năm' hoặc cột bên trái).\n"
        "Bước 2: Đọc từng dòng theo mã VAS — "
        "110=tiền&TĐ_tiền, 130=phải_thu_NH, 140=hàng_tồn_kho, "
        "100=TSNH_tổng, 200=TSDH_tổng, 270=tổng_tài_sản, "
        "310=nợ_NH, 330=nợ_DH, 300=tổng_nợ, 400=vốn_CSH.\n"
        "Bước 3: Chuyển đơn vị VND → triệu đồng (chia 1.000.000). "
        "Dấu CHẤM ngăn nghìn: 1.750.574.054.602 = 1750574 triệu. "
        "Số trong ngoặc = âm: (123.456) → -123,456 triệu.\n"
        "Bước 4: Kiểm tra: tổng_tài_sản ≈ tổng_nợ + vốn_CSH (±2%). "
        f"Nếu equity bị thiếu → tính equity = total_assets - total_liabilities.\n"
        "Bước 5: Trả về JSON thuần túy (bắt đầu '{', không markdown):\n"
        '{"total_assets":null,"current_assets":null,"cash_and_equivalents":null,'
        '"short_term_receivables":null,"inventories":null,"non_current_assets":null,'
        '"fixed_assets":null,"total_liabilities":null,"current_liabilities":null,'
        '"long_term_liabilities":null,"equity":null,"charter_capital_amount":null}'
    )

    stage1_result: dict = {}
    for attempt in range(1, 3):
        raw = _invoke_with_retry(
            llm,
            [SystemMessage(content=cdkt_system),
             HumanMessage(content=f"Năm: {year}\n\nCĐKT:\n\n{cdkt_text}")],
            context=f"financial parse year={year} stage1 attempt={attempt}",
        )
        if not raw:
            logger.warning(f"Stage 1 empty response year={year} attempt={attempt}")
            continue
        raw = _strip_fences(raw)
        if not raw.startswith("{"):
            logger.warning(f"Stage 1 non-JSON year={year} attempt={attempt}: {raw[:80]!r}")
            continue
        try:
            # repair_json handles both truncated output (token limit) and
            # trailing commentary that some models append after '}'.
            stage1_result = json.loads(repair_json(raw.strip()))
            logger.debug(
                f"Stage 1 OK year={year}: total_assets={stage1_result.get('total_assets')}, "
                f"equity={stage1_result.get('equity')}"
            )
            break
        except json.JSONDecodeError as e:
            logger.warning(f"Stage 1 JSON error year={year} attempt={attempt}: {e}  raw={raw[:120]!r}")

    if not stage1_result:
        raise ValueError(f"Could not parse CĐKT (stage 1) for year {year}")

    # ------------------------------------------------------------------ #
    # Stage 2 — income statement + cash flow (KQKD / LCTT)               #
    # ------------------------------------------------------------------ #
    _KQKD_MAX = 5000
    stage2_result: dict = {}

    if kqkd_pos is not None:
        kqkd_text = text[kqkd_pos : kqkd_pos + _KQKD_MAX]

        # Chain-of-thought for income statement
        kqkd_system = (
            f"Phân tích và trích xuất số liệu KẾT QUẢ HOẠT ĐỘNG KINH DOANH năm {year}.\n\n"
            "Thực hiện TỪNG BƯỚC:\n"
            f"Bước 1: Tìm tiêu đề 'KẾT QUẢ HOẠT ĐỘNG KINH DOANH' và xác định cột năm {year}.\n"
            "Bước 2: Đọc từng dòng theo mã VAS — "
            "10=doanh_thu_thuần, 11=giá_vốn, 20=LN_gộp, "
            "25=CP_bán_hàng, 26=CP_QLDN, 30=LN_HĐKD, "
            "50=LN_trước_thuế, 60=LN_sau_thuế.\n"
            "Bước 3: Tìm bảng LƯU CHUYỂN TIỀN TỆ — "
            "dòng tiền từ HĐKD, HĐĐT, HĐTC.\n"
            "Bước 4: Chuyển đơn vị VND → triệu đồng. Số trong ngoặc = âm.\n"
            "Bước 5: Trả về JSON thuần túy (bắt đầu '{', không markdown):\n"
            '{"net_revenue":null,"gross_profit":null,"operating_profit":null,'
            '"profit_before_tax":null,"net_profit":null,"cost_of_goods_sold":null,'
            '"selling_expenses":null,"admin_expenses":null,"operating_cash_flow":null,'
            '"investing_cash_flow":null,"financing_cash_flow":null}'
        )

        for attempt in range(1, 3):
            raw = _invoke_with_retry(
                llm,
                [SystemMessage(content=kqkd_system),
                 HumanMessage(content=f"Năm: {year}\n\nKQKD/LCTT:\n\n{kqkd_text}")],
                context=f"financial parse year={year} stage2 attempt={attempt}",
            )
            if not raw:
                logger.warning(f"Stage 2 empty response year={year} attempt={attempt}")
                continue
            raw = _strip_fences(raw)
            if not raw.startswith("{"):
                logger.warning(f"Stage 2 non-JSON year={year} attempt={attempt}: {raw[:80]!r}")
                continue
            try:
                stage2_result = json.loads(repair_json(raw.strip()))
                logger.debug(
                    f"Stage 2 OK year={year}: net_revenue={stage2_result.get('net_revenue')}, "
                    f"net_profit={stage2_result.get('net_profit')}"
                )
                break
            except json.JSONDecodeError as e:
                logger.warning(f"Stage 2 JSON error year={year} attempt={attempt}: {e}  raw={raw[:120]!r}")

        if not stage2_result:
            logger.warning(f"Stage 2 failed year={year} — income statement fields will be null")
    else:
        logger.warning(f"Skipping stage 2 year={year} — no KQKD section found in text")

    # ------------------------------------------------------------------ #
    # Merge both stages                                                   #
    # ------------------------------------------------------------------ #
    merged = {**stage1_result, **stage2_result, "year": year}
    return merged


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def extract_pdf_financial_tables(
    pdf_base_dir: str,
    company: str = "unknown",
) -> dict[int, dict]:
    """Extract financial tables from PDFs for years 2022, 2023, and 2024.

    Directory structure expected:
        <pdf_base_dir>/
            2022/<single .pdf file>
            2023/<single .pdf file>
            2024/<single .pdf file>

    Args:
        pdf_base_dir: Path to the directory containing per-year subdirectories.
        company:      Company identifier used for cache namespacing (e.g. "mst").

    OCR results are cached under:
        data/cache/ocr/{company}/{year}/{strategy}/{YYYYMMDD}_vN/

    On re-runs within the same day the cached text is returned immediately,
    skipping the OCR engine entirely.

    Returns dict keyed by year (int). Missing years are skipped silently.
    """
    results: dict[int, dict] = {}

    for year in [2022, 2023, 2024]:
        year_dir  = Path(pdf_base_dir) / str(year)
        if not year_dir.exists():
            logger.warning(f"{year_dir} does not exist — skipping {year}")
            continue

        pdf_files = list(year_dir.glob("*.pdf"))
        if not pdf_files:
            logger.warning(f"No PDF files in {year_dir} — skipping {year}")
            continue

        pdf_path = str(pdf_files[0])
        logger.info(f"Processing year {year}: {pdf_path}")

        text = _convert_pdf_to_text(pdf_path, company, year)
        if not text.strip():
            logger.warning(f"Skipping {year} — could not extract any text")
            continue

        relevant_text = _extract_financial_sections(text)
        logger.debug(f"Year {year}: relevant sections = {len(relevant_text)} chars")

        try:
            financial_dict = _parse_financial_data_with_llm(relevant_text, year)

            # Post-processing: fix unit errors and fill derivable fields (no LLM)
            financial_dict = _normalize_units(financial_dict, year)
            financial_dict = _fill_derived_fields(financial_dict, year)

            results[year]  = financial_dict
            # Persist extracted data alongside OCR cache for debugging
            _cache.save_financial_data(company, year, financial_dict)
            logger.info(
                f"Year {year} extracted  "
                f"net_revenue={financial_dict.get('net_revenue')}  "
                f"total_assets={financial_dict.get('total_assets')}"
            )
        except Exception as e:
            logger.error(f"LLM parsing failed for {year}: {e}", exc_info=True)

    return results
