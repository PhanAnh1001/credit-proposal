"""OCR result cache — persist per-page and full-text results to disk.

Directory layout
----------------
data/cache/ocr/
└── {company}/
    └── {year}/                    # BCTC year: 2022 | 2023 | 2024
        └── {strategy}/            # pymupdf | markitdown | vision_llm
            └── {YYYYMMDD}_v{N}/   # e.g. 20260412_v1, 20260412_v2
                ├── meta.json      # run metadata (pdf hash, elapsed, page list…)
                ├── full_text.txt  # combined text of all OCR'd pages
                └── pages/
                    ├── page_001.txt
                    ├── page_002.txt
                    └── …

Usage
-----
    cache   = OcrCache()                                    # base: docs/ocr-cache
    run_dir = cache.new_run(company, year, strategy)        # create versioned dir

    # save incrementally during OCR
    cache.save_page(run_dir, page_num=3, text="…")
    cache.save_full_text(run_dir, full_text)
    cache.save_meta(run_dir, {"elapsed_s": 45.2, …})

    # load latest cached result (today's most recent version)
    text = cache.load_latest_full_text(company, year, strategy)
    if text:
        ...  # skip OCR, use cached text
"""
import hashlib
import json
from datetime import date
from pathlib import Path

from .logger import get_logger
from ..config import OCR_CACHE_DIR

logger = get_logger("ocr_cache")

_DEFAULT_BASE = str(OCR_CACHE_DIR)


def _pdf_sha256(pdf_path: str, chunk: int = 65536) -> str:
    """Return first 12 hex chars of SHA-256 of the PDF file (fast fingerprint)."""
    h = hashlib.sha256()
    try:
        with open(pdf_path, "rb") as f:
            while data := f.read(chunk):
                h.update(data)
        return h.hexdigest()[:12]
    except OSError:
        return "unknown"


class OcrCache:
    """File-based cache for OCR results.

    Thread-safety: not guaranteed — intended for single-process use.
    """

    def __init__(self, base_dir: str = _DEFAULT_BASE) -> None:
        self.base_dir = Path(base_dir)

    # ------------------------------------------------------------------
    # Internal path helpers
    # ------------------------------------------------------------------

    def _strategy_dir(self, company: str, year: int, strategy: str) -> Path:
        return self.base_dir / company / str(year) / strategy

    def _today_str(self) -> str:
        return date.today().strftime("%Y%m%d")

    def _next_version(self, strategy_dir: Path, today: str) -> int:
        """Return the next available version number for today."""
        existing = [
            p for p in strategy_dir.glob(f"{today}_v*") if p.is_dir()
        ]
        if not existing:
            return 1
        versions = []
        for p in existing:
            try:
                versions.append(int(p.name.split("_v")[-1]))
            except ValueError:
                pass
        return max(versions, default=0) + 1

    def _run_dir(self, company: str, year: int, strategy: str,
                 today: str, version: int) -> Path:
        return self._strategy_dir(company, year, strategy) / f"{today}_v{version}"

    # ------------------------------------------------------------------
    # Write API
    # ------------------------------------------------------------------

    def new_run(
        self,
        company: str,
        year: int,
        strategy: str,
        pdf_path: str | None = None,
    ) -> Path:
        """Create a new versioned run directory and return its path.

        Args:
            company:  Company identifier, e.g. "mst".
            year:     BCTC year, e.g. 2024.
            strategy: OCR strategy name — "pymupdf" | "markitdown" | "vision_llm".
            pdf_path: Optional source PDF path — used to record file hash.

        Returns:
            Path to the new run directory (already created on disk).
        """
        today   = self._today_str()
        sdir    = self._strategy_dir(company, year, strategy)
        version = self._next_version(sdir, today)
        run_dir = self._run_dir(company, year, strategy, today, version)

        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "pages").mkdir(exist_ok=True)

        # Write skeleton meta immediately so the directory is identifiable
        meta: dict = {
            "company":   company,
            "year":      year,
            "strategy":  strategy,
            "date":      today,
            "version":   version,
            "pdf_path":  str(pdf_path) if pdf_path else None,
            "pdf_hash":  _pdf_sha256(pdf_path) if pdf_path else None,
            "status":    "in_progress",
        }
        self.save_meta(run_dir, meta)

        logger.debug(
            f"OCR cache run created: {run_dir.relative_to(self.base_dir.parent)}"
        )
        return run_dir

    def save_page(self, run_dir: Path, page_num: int, text: str) -> None:
        """Save OCR text for a single page.

        Args:
            page_num: 1-based page number (as printed in the document).
        """
        page_file = run_dir / "pages" / f"page_{page_num:03d}.txt"
        page_file.write_text(text, encoding="utf-8")

    def save_full_text(self, run_dir: Path, text: str) -> None:
        """Save the combined full-text result for this run."""
        (run_dir / "full_text.txt").write_text(text, encoding="utf-8")
        # Mark as completed
        self._update_meta(run_dir, {"status": "completed"})
        logger.debug(
            f"OCR cache saved: {run_dir.name}  ({len(text)} chars)"
        )

    def save_meta(self, run_dir: Path, meta: dict) -> None:
        """Write (overwrite) meta.json for this run."""
        (run_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _update_meta(self, run_dir: Path, updates: dict) -> None:
        meta_path = run_dir / "meta.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                meta = {}
        else:
            meta = {}
        meta.update(updates)
        self.save_meta(run_dir, meta)

    def finish_run(
        self,
        run_dir: Path,
        elapsed_s: float,
        total_pages: int,
        ocr_pages: list[int],
    ) -> None:
        """Record final timing + page stats on run completion."""
        self._update_meta(run_dir, {
            "status":      "completed",
            "elapsed_s":   round(elapsed_s, 2),
            "total_pages": total_pages,
            "ocr_pages":   ocr_pages,
        })

    # ------------------------------------------------------------------
    # Read API
    # ------------------------------------------------------------------

    def find_latest_run(
        self,
        company: str,
        year: int,
        strategy: str,
        today_only: bool = True,
    ) -> Path | None:
        """Return the most recent completed run directory, or None.

        Args:
            today_only: If True (default), only consider runs from today.
                        Set False to load any past run.
        """
        sdir = self._strategy_dir(company, year, strategy)
        if not sdir.exists():
            return None

        prefix = self._today_str() if today_only else ""
        candidates = sorted(
            [p for p in sdir.iterdir() if p.is_dir() and p.name.startswith(prefix)],
            key=lambda p: p.name,
            reverse=True,
        )

        for run_dir in candidates:
            meta_path = run_dir / "meta.json"
            full_text  = run_dir / "full_text.txt"
            if not full_text.exists():
                continue
            # Prefer completed runs; fall through if in_progress is the only option
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    if meta.get("status") == "completed":
                        return run_dir
                except Exception:
                    pass

        return None

    def load_latest_full_text(
        self,
        company: str,
        year: int,
        strategy: str,
        today_only: bool = True,
    ) -> str | None:
        """Return the full_text from the most recent completed run, or None."""
        run_dir = self.find_latest_run(company, year, strategy, today_only)
        if run_dir is None:
            return None

        full_text_path = run_dir / "full_text.txt"
        try:
            text = full_text_path.read_text(encoding="utf-8")
            logger.info(
                f"Cache HIT: {company}/{year}/{strategy} "
                f"← {run_dir.name}  ({len(text)} chars)"
            )
            return text
        except OSError as e:
            logger.warning(f"Cache read failed: {e}")
            return None

    def load_page(
        self,
        company: str,
        year: int,
        strategy: str,
        page_num: int,
        today_only: bool = True,
    ) -> str | None:
        """Return cached text for a specific page, or None."""
        run_dir = self.find_latest_run(company, year, strategy, today_only)
        if run_dir is None:
            return None

        page_file = run_dir / "pages" / f"page_{page_num:03d}.txt"
        if page_file.exists():
            return page_file.read_text(encoding="utf-8")
        return None

    # ------------------------------------------------------------------
    # Inspection helpers
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Year-level data persistence (TOC text, extracted financials, ratios)
    # ------------------------------------------------------------------

    def _year_dir(self, company: str, year: int) -> Path:
        return self.base_dir / company / str(year)

    def save_toc_text(self, company: str, year: int, text: str) -> None:
        """Save the raw TOC page text used for section-targeting."""
        path = self._year_dir(company, year) / "toc_text.txt"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        logger.debug(f"TOC text saved: {path.relative_to(self.base_dir.parent)}")

    def save_financial_data(self, company: str, year: int, data: dict) -> None:
        """Save LLM-extracted financial dict (raw, in triệu đồng) for a year."""
        path = self._year_dir(company, year) / "financial_data.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.debug(f"Financial data saved: {path.relative_to(self.base_dir.parent)}")

    def save_ratios(self, company: str, year: int, ratios: dict) -> None:
        """Save calculated financial ratios for a year."""
        path = self._year_dir(company, year) / "ratios.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(ratios, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.debug(f"Ratios saved: {path.relative_to(self.base_dir.parent)}")

    def load_financial_data(self, company: str, year: int) -> dict | None:
        """Load previously saved financial data, or None if not found."""
        path = self._year_dir(company, year) / "financial_data.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    # ------------------------------------------------------------------

    def list_runs(
        self, company: str | None = None, year: int | None = None
    ) -> list[dict]:
        """List all cached runs, optionally filtered by company/year.

        Returns list of dicts with keys: company, year, strategy, run, status.
        """
        runs: list[dict] = []
        base = self.base_dir

        if not base.exists():
            return runs

        companies = [base / company] if company else list(base.iterdir())
        for c_path in companies:
            if not c_path.is_dir():
                continue
            years = [c_path / str(year)] if year else list(c_path.iterdir())
            for y_path in years:
                if not y_path.is_dir():
                    continue
                for s_path in y_path.iterdir():
                    if not s_path.is_dir():
                        continue
                    for run_dir in sorted(s_path.iterdir()):
                        if not run_dir.is_dir():
                            continue
                        meta: dict = {}
                        mp = run_dir / "meta.json"
                        if mp.exists():
                            try:
                                meta = json.loads(mp.read_text(encoding="utf-8"))
                            except Exception:
                                pass
                        runs.append({
                            "company":    c_path.name,
                            "year":       y_path.name,
                            "strategy":   s_path.name,
                            "run":        run_dir.name,
                            "status":     meta.get("status", "unknown"),
                            "elapsed_s":  meta.get("elapsed_s"),
                            "ocr_pages":  meta.get("ocr_pages"),
                            "path":       str(run_dir),
                        })
        return runs
