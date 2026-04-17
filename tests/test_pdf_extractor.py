"""Tests for PDF extractor helper functions — no LLM, no file I/O."""
import base64
from io import BytesIO

import pytest


class TestTocRegexParsing:
    """Test TOC regex parser without any API calls."""

    def setup_method(self):
        from src.tools.pdf_extractor import _parse_toc_with_regex
        self.parse = _parse_toc_with_regex

    def test_parse_balance_sheet_page(self):
        toc_text = "Bảng cân đối kế toán ...... 45\n"
        result = self.parse(toc_text, total_pages=100)
        assert result is not None
        assert "balance_sheet" in result
        assert result["balance_sheet"] == 44  # 0-based: page 45 → index 44

    def test_parse_income_statement_page(self):
        toc_text = "Kết quả hoạt động kinh doanh ........ 52\n"
        result = self.parse(toc_text, total_pages=100)
        assert result is not None
        assert "income_statement" in result
        assert result["income_statement"] == 51

    def test_parse_both_statements(self):
        toc_text = (
            "Bảng cân đối kế toán ........... 30\n"
            "Kết quả hoạt động kinh doanh ... 35\n"
        )
        result = self.parse(toc_text, total_pages=100)
        assert result is not None
        assert "balance_sheet" in result
        assert "income_statement" in result

    def test_returns_none_when_no_match(self):
        toc_text = "Không có mục lục tài chính ở đây"
        result = self.parse(toc_text, total_pages=100)
        assert result is None

    def test_returns_none_when_keywords_present_but_no_page_numbers(self):
        # MST 2024 format: section titles listed without page numbers
        toc_text = (
            "MỤC LỤC\n"
            "Bảng cân đối kế toán\n"
            "Báo cáo kết quả hoạt động kinh doanh\n"
            "Báo cáo lưu chuyển tiền tệ\n"
            "Bản thuyết minh báo cáo tài chính\n"
        )
        result = self.parse(toc_text, total_pages=44)
        # No page numbers → regex must return None; LLM skip is handled by caller
        assert result is None

    def test_page_clamped_to_valid_range(self):
        # Page 150 in a 100-page PDF → clamped to index 99
        toc_text = "Bảng cân đối kế toán .... 150\n"
        result = self.parse(toc_text, total_pages=100)
        assert result is not None
        assert result["balance_sheet"] == 99  # max(0, min(149, 99))


class TestTocHasSectionKeywords:
    """Test the keyword-only TOC detector."""

    def setup_method(self):
        from src.tools.pdf_extractor import _toc_has_section_keywords
        self.check = _toc_has_section_keywords

    def test_detects_balance_sheet_keyword(self):
        assert self.check("Bảng cân đối kế toán\n") is True

    def test_detects_income_statement_keyword(self):
        assert self.check("Kết quả hoạt động kinh doanh\n") is True

    def test_detects_cash_flow_keyword(self):
        assert self.check("Báo cáo lưu chuyển tiền tệ\n") is True

    def test_no_keywords_returns_false(self):
        assert self.check("Không có mục lục tài chính ở đây\n") is False

    def test_case_insensitive(self):
        assert self.check("BẢNG CÂN ĐỐI KẾ TOÁN\n") is True


class TestNormalizeUnits:
    def setup_method(self):
        from src.tools.pdf_extractor import _normalize_units
        self.normalize = _normalize_units

    def test_no_normalization_needed(self):
        d = {"total_assets": 500_000.0, "net_revenue": 200_000.0}
        result = self.normalize(d, year=2023)
        assert result["total_assets"] == 500_000.0

    def test_divides_by_million_on_anomaly(self):
        # total_assets > 1e10 → must be raw VND, should be divided by 1e6
        d = {"total_assets": 5e12, "net_revenue": 2e12}
        result = self.normalize(d, year=2023)
        assert result["total_assets"] == pytest.approx(5_000_000.0)
        assert result["net_revenue"] == pytest.approx(2_000_000.0)


class TestDetectPdfType:
    """Test PDF type detection — fitz.open is mocked, no real PDF needed."""

    def _make_mock_doc(self, page_texts: list[str]):
        """Build a minimal fitz-like mock document with given per-page texts."""
        from unittest.mock import MagicMock

        pages = []
        for text in page_texts:
            p = MagicMock()
            p.get_text.return_value = text
            pages.append(p)

        doc = MagicMock()
        doc.__len__ = MagicMock(return_value=len(pages))
        doc.__getitem__ = MagicMock(side_effect=lambda idx: pages[idx])
        return doc

    def test_all_text_pages_returns_text(self):
        import fitz
        from unittest.mock import patch

        from src.tools.pdf_extractor import _detect_pdf_type

        # 10 pages all with substantial text
        mock_doc = self._make_mock_doc(["A" * 200] * 10)
        with patch.object(fitz, "open", return_value=mock_doc):
            result = _detect_pdf_type("dummy.pdf")
        assert result == "text"

    def test_all_empty_pages_returns_image(self):
        import fitz
        from unittest.mock import patch

        from src.tools.pdf_extractor import _detect_pdf_type

        # 10 pages all with no text (image-based PDF)
        mock_doc = self._make_mock_doc([""] * 10)
        with patch.object(fitz, "open", return_value=mock_doc):
            result = _detect_pdf_type("dummy.pdf")
        assert result == "image"

    def test_mostly_image_pages_returns_image(self):
        import fitz
        from unittest.mock import patch

        from src.tools.pdf_extractor import _detect_pdf_type

        # 10 pages; _SAMPLE_N_PAGES=5 with step=2 → sampled indices [0,2,4,6,8,9].
        # Only index 0 has text → ratio = 1/6 ≈ 0.17 ≤ (1 - 0.7) → "image".
        texts = [""] * 10
        texts[0] = "A" * 200
        mock_doc = self._make_mock_doc(texts)
        with patch.object(fitz, "open", return_value=mock_doc):
            result = _detect_pdf_type("dummy.pdf")
        assert result == "image"

    def test_mixed_pages_returns_mixed(self):
        import fitz
        from unittest.mock import patch

        from src.tools.pdf_extractor import _detect_pdf_type

        # Half-and-half: ratio ≈ 0.5 → "mixed"
        texts = ["A" * 200] * 5 + [""] * 5
        mock_doc = self._make_mock_doc(texts)
        with patch.object(fitz, "open", return_value=mock_doc):
            result = _detect_pdf_type("dummy.pdf")
        assert result == "mixed"

    def test_exception_returns_unknown(self):
        import fitz
        from unittest.mock import patch

        from src.tools.pdf_extractor import _detect_pdf_type

        with patch.object(fitz, "open", side_effect=Exception("cannot open")):
            result = _detect_pdf_type("dummy.pdf")
        assert result == "unknown"

    def test_below_min_chars_threshold_counts_as_no_text(self):
        import fitz
        from unittest.mock import patch

        from src.tools.pdf_extractor import _detect_pdf_type, _MIN_TEXT_CHARS_PAGE

        # Pages with just below the threshold → should count as "no text"
        short_text = "A" * (_MIN_TEXT_CHARS_PAGE - 1)
        mock_doc = self._make_mock_doc([short_text] * 10)
        with patch.object(fitz, "open", return_value=mock_doc):
            result = _detect_pdf_type("dummy.pdf")
        assert result == "image"


class TestPreprocessPageImage:
    """Test image preprocessing — PIL only, no file I/O, no LLM."""

    def _make_png_b64(self, width: int = 60, height: int = 60, shade: int = 128) -> str:
        """Create a solid-grey PNG and return it as a base64 string."""
        from PIL import Image

        img = Image.new("RGB", (width, height), color=shade)
        buf = BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()

    def _make_gradient_png_b64(self, size: int = 80) -> str:
        """Create a grayscale gradient PNG (realistic for financial scans)."""
        from PIL import Image

        img = Image.new("L", (size, size))
        pixels = img.load()
        for y in range(size):
            for x in range(size):
                pixels[x, y] = (x + y * 2) % 256
        img = img.convert("RGB")
        buf = BytesIO()
        img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode()

    def test_returns_valid_base64_string(self):
        from src.tools.pdf_extractor import _preprocess_page_image

        img_b64 = self._make_png_b64()
        result  = _preprocess_page_image(img_b64)

        assert isinstance(result, str)
        # Must decode without error and start with PNG magic bytes
        decoded = base64.b64decode(result)
        assert decoded[:4] == b"\x89PNG"

    def test_handles_invalid_input_gracefully(self):
        """Bad base64 input → original string returned unchanged."""
        from src.tools.pdf_extractor import _preprocess_page_image

        bad = "not_valid_base64!!!"
        result = _preprocess_page_image(bad)
        assert result == bad

    def test_output_differs_from_input_for_gradient_image(self):
        """Preprocessing must change pixel content (not a no-op)."""
        from src.tools.pdf_extractor import _preprocess_page_image
        from PIL import Image

        img_b64 = self._make_gradient_png_b64()
        result  = _preprocess_page_image(img_b64)

        # Same format (PNG), but pixel content should differ due to enhancement
        orig = Image.open(BytesIO(base64.b64decode(img_b64))).convert("RGB")
        proc = Image.open(BytesIO(base64.b64decode(result))).convert("RGB")

        # At least some pixels must have changed
        orig_bytes = list(orig.tobytes())
        proc_bytes = list(proc.tobytes())
        differences = sum(1 for a, b in zip(orig_bytes, proc_bytes) if a != b)
        assert differences > 0, "Preprocessing produced identical pixel data"

    def test_low_contrast_image_gets_higher_contrast(self):
        """A washed-out image should have a larger pixel range after processing."""
        from src.tools.pdf_extractor import _preprocess_page_image
        from PIL import Image

        # Washed-out image: all pixels in narrow [100, 140] range
        img = Image.new("L", (80, 80))
        pixels = img.load()
        for y in range(80):
            for x in range(80):
                pixels[x, y] = 100 + (x + y) % 40  # range [100, 139]
        img = img.convert("RGB")
        buf = BytesIO()
        img.save(buf, format="PNG")
        img_b64 = base64.b64encode(buf.getvalue()).decode()

        result = _preprocess_page_image(img_b64)
        proc   = Image.open(BytesIO(base64.b64decode(result))).convert("L")

        proc_pixels = list(proc.tobytes())
        pixel_range = max(proc_pixels) - min(proc_pixels)
        # After auto-contrast + contrast boost, the range should be much wider
        assert pixel_range > 50, f"Expected wider pixel range after processing, got {pixel_range}"


class TestExtractFinancialSections:
    def setup_method(self):
        from src.tools.pdf_extractor import _extract_financial_sections
        self.extract = _extract_financial_sections

    def test_extracts_balance_sheet_section(self):
        text = (
            "Trang 1: Thông tin chung\n"
            "BẢNG CÂN ĐỐI KẾ TOÁN\n"
            "Tài sản ngắn hạn: 100\n"
            "Tổng tài sản: 500\n"
        )
        result = self.extract(text)
        assert "BẢNG CÂN ĐỐI KẾ TOÁN" in result
        assert "Tổng tài sản" in result

    def test_falls_back_to_head_lines_when_no_keywords(self):
        text = "\n".join(f"Line {i}" for i in range(1000))
        result = self.extract(text)
        assert result  # Should return something (head lines)
