"""
Tests for font resolution utilities (no API calls, no PDF generation):
  - _fonts_dir()
  - _emit_warning()
  - get_docx_font()
  - get_pdf_font() (ImportError path only)
"""

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch


import src.output.font_resolver as fr


# ---------------------------------------------------------------------------
# _fonts_dir
# ---------------------------------------------------------------------------


class TestFontsDir:

    def test_returns_path_object(self):
        result = fr._fonts_dir()
        assert isinstance(result, Path)

    def test_ends_with_fonts_directory(self):
        result = fr._fonts_dir()
        assert result.name == "fonts"

    def test_parent_is_project_root(self):
        result = fr._fonts_dir()
        # The project root should contain main.py
        root = result.parent
        assert (root / "main.py").exists()


# ---------------------------------------------------------------------------
# _emit_warning
# ---------------------------------------------------------------------------


class TestEmitWarning:

    def test_prints_message_to_stdout(self, capsys):
        fr._emit_warning("Test warning")
        out = capsys.readouterr().out
        assert "Test warning" in out

    def test_logs_message_when_no_log_message(self, caplog):
        with caplog.at_level(logging.WARNING):
            fr._emit_warning("Warning text")
        assert "Warning text" in caplog.text

    def test_logs_custom_log_message_when_provided(self, caplog):
        with caplog.at_level(logging.WARNING):
            fr._emit_warning("Printed text", log_message="Log only text")
        assert "Log only text" in caplog.text

    def test_printed_message_is_user_facing(self, capsys):
        fr._emit_warning("User message", log_message="Internal log")
        out = capsys.readouterr().out
        assert "User message" in out
        # The internal log message should not appear in printed output
        assert "Internal log" not in out


# ---------------------------------------------------------------------------
# get_docx_font
# ---------------------------------------------------------------------------


class TestGetDocxFont:

    def test_no_custom_font_returns_arial_unicode_ms(self):
        result = fr.get_docx_font()
        assert result == "Arial Unicode MS"

    def test_none_custom_font_returns_arial_unicode_ms(self):
        result = fr.get_docx_font(None)
        assert result == "Arial Unicode MS"

    def test_custom_font_found_in_fonts_dir_returns_it(self, tmp_path):
        # Create a fake .ttf file in a temp directory mimicking fonts/
        (tmp_path / "MyFont.ttf").write_bytes(b"\x00" * 16)
        with patch.object(fr, "_fonts_dir", return_value=tmp_path):
            result = fr.get_docx_font("MyFont")
        assert result == "MyFont"

    def test_custom_font_missing_emits_warning_and_falls_back(self, tmp_path, capsys):
        # fonts dir exists but the requested font file is absent
        with patch.object(fr, "_fonts_dir", return_value=tmp_path):
            result = fr.get_docx_font("MissingFont")
        out = capsys.readouterr().out
        assert "Warning" in out
        assert result == "Arial Unicode MS"

    def test_fonts_dir_does_not_exist_falls_back_to_default(self, tmp_path):
        non_existent = tmp_path / "no_such_dir"
        with patch.object(fr, "_fonts_dir", return_value=non_existent):
            result = fr.get_docx_font("AnyFont")
        assert result == "Arial Unicode MS"


# ---------------------------------------------------------------------------
# get_pdf_font (ImportError path only — no reportlab needed)
# ---------------------------------------------------------------------------


class TestGetPdfFontImportError:

    def test_returns_times_roman_when_reportlab_unavailable(self):
        # Simulate an environment where reportlab cannot be imported
        with patch.dict("sys.modules", {"reportlab": None,
                                        "reportlab.pdfbase": None,
                                        "reportlab.pdfbase.pdfmetrics": None,
                                        "reportlab.pdfbase.ttfonts": None}):
            result = fr.get_pdf_font()
        assert result == "Times-Roman"


# ---------------------------------------------------------------------------
# get_pdf_font (with reportlab available — mock pdfmetrics registration)
# ---------------------------------------------------------------------------


class TestGetPdfFontWithReportlab:
    """Tests for get_pdf_font() code paths that run when reportlab is installed."""

    @staticmethod
    def _pdf_patches(tmp_path, registered=None):
        """Return a list of context managers that mock font registration."""
        registered = registered or []
        return [
            patch.object(fr, "_fonts_dir", return_value=tmp_path),
            patch("reportlab.pdfbase.pdfmetrics.getRegisteredFontNames", return_value=registered),
            patch("reportlab.pdfbase.pdfmetrics.registerFont"),
            patch("reportlab.pdfbase.ttfonts.TTFont"),
        ]

    def test_custom_font_found_returns_custom_name(self, tmp_path):
        (tmp_path / "MyFont.ttf").touch()
        with (
            patch.object(fr, "_fonts_dir", return_value=tmp_path),
            patch("reportlab.pdfbase.pdfmetrics.getRegisteredFontNames", return_value=[]),
            patch("reportlab.pdfbase.pdfmetrics.registerFont"),
            patch("reportlab.pdfbase.ttfonts.TTFont"),
        ):
            result = fr.get_pdf_font("MyFont")
        assert result == "CustomFont_MyFont"

    def test_custom_font_already_registered_skips_registration(self, tmp_path):
        (tmp_path / "MyFont.ttf").touch()
        with (
            patch.object(fr, "_fonts_dir", return_value=tmp_path),
            patch("reportlab.pdfbase.pdfmetrics.getRegisteredFontNames",
                  return_value=["CustomFont_MyFont"]),
            patch("reportlab.pdfbase.pdfmetrics.registerFont") as mock_reg,
            patch("reportlab.pdfbase.ttfonts.TTFont"),
        ):
            result = fr.get_pdf_font("MyFont")
        mock_reg.assert_not_called()
        assert result == "CustomFont_MyFont"

    def test_custom_font_registration_error_warns_and_falls_through(self, tmp_path, capsys):
        (tmp_path / "BadFont.ttf").touch()
        with (
            patch.object(fr, "_fonts_dir", return_value=tmp_path),
            patch("reportlab.pdfbase.pdfmetrics.getRegisteredFontNames", return_value=[]),
            patch("reportlab.pdfbase.pdfmetrics.registerFont", side_effect=OSError("bad")),
            patch("reportlab.pdfbase.ttfonts.TTFont"),
        ):
            result = fr.get_pdf_font("BadFont")
        out = capsys.readouterr().out
        assert "Warning" in out
        # Falls through to preferred/glob loops; empty tmp_path → Times-Roman
        assert result == "Times-Roman"

    def test_custom_font_file_not_found_warns_and_continues(self, tmp_path, capsys):
        # No file in tmp_path matching the requested font
        with (
            patch.object(fr, "_fonts_dir", return_value=tmp_path),
            patch("reportlab.pdfbase.pdfmetrics.getRegisteredFontNames", return_value=[]),
            patch("reportlab.pdfbase.pdfmetrics.registerFont"),
            patch("reportlab.pdfbase.ttfonts.TTFont"),
        ):
            result = fr.get_pdf_font("MissingFont")
        out = capsys.readouterr().out
        assert "Warning" in out
        assert result == "Times-Roman"

    def test_preferred_font_found_returns_name(self, tmp_path):
        (tmp_path / "Arial Unicode.ttf").touch()
        with (
            patch.object(fr, "_fonts_dir", return_value=tmp_path),
            patch("reportlab.pdfbase.pdfmetrics.getRegisteredFontNames", return_value=[]),
            patch("reportlab.pdfbase.pdfmetrics.registerFont"),
            patch("reportlab.pdfbase.ttfonts.TTFont"),
        ):
            result = fr.get_pdf_font()
        assert result == "ArialUnicode"

    def test_preferred_font_error_skips_to_next(self, tmp_path):
        # AppleGothic fails, AppleMyungjo succeeds
        (tmp_path / "AppleGothic.ttf").touch()
        (tmp_path / "AppleMyungjo.ttf").touch()

        call_count = {"n": 0}

        def maybe_fail(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise OSError("bad ttf")

        with (
            patch.object(fr, "_fonts_dir", return_value=tmp_path),
            patch("reportlab.pdfbase.pdfmetrics.getRegisteredFontNames", return_value=[]),
            patch("reportlab.pdfbase.pdfmetrics.registerFont", side_effect=maybe_fail),
            patch("reportlab.pdfbase.ttfonts.TTFont"),
        ):
            result = fr.get_pdf_font()
        assert result == "AppleMyungjo"

    def test_glob_fallback_returns_non_preferred_font(self, tmp_path):
        # Only a non-preferred font is available → glob fallback picks it
        (tmp_path / "UnknownFont.ttf").touch()
        with (
            patch.object(fr, "_fonts_dir", return_value=tmp_path),
            patch("reportlab.pdfbase.pdfmetrics.getRegisteredFontNames", return_value=[]),
            patch("reportlab.pdfbase.pdfmetrics.registerFont"),
            patch("reportlab.pdfbase.ttfonts.TTFont"),
        ):
            result = fr.get_pdf_font()
        assert result == "UnknownFont"

    def test_no_fonts_available_warns_and_returns_times_roman(self, tmp_path, capsys):
        # fonts_dir exists but is totally empty
        with (
            patch.object(fr, "_fonts_dir", return_value=tmp_path),
            patch("reportlab.pdfbase.pdfmetrics.getRegisteredFontNames", return_value=[]),
            patch("reportlab.pdfbase.pdfmetrics.registerFont"),
            patch("reportlab.pdfbase.ttfonts.TTFont"),
        ):
            result = fr.get_pdf_font()
        out = capsys.readouterr().out
        assert "No CJK fonts available" in out or "Warning" in out
        assert result == "Times-Roman"

    def test_fonts_dir_not_exist_returns_times_roman(self, tmp_path, capsys):
        non_existent = tmp_path / "no_such_dir"
        with (
            patch.object(fr, "_fonts_dir", return_value=non_existent),
            patch("reportlab.pdfbase.pdfmetrics.getRegisteredFontNames", return_value=[]),
            patch("reportlab.pdfbase.pdfmetrics.registerFont"),
            patch("reportlab.pdfbase.ttfonts.TTFont"),
        ):
            result = fr.get_pdf_font()
        assert result == "Times-Roman"

    def test_oserror_during_font_check_returns_times_roman(self):
        mock_path = MagicMock()
        mock_path.exists.side_effect = OSError("permission denied")
        with (
            patch.object(fr, "_fonts_dir", return_value=mock_path),
            patch("reportlab.pdfbase.pdfmetrics.getRegisteredFontNames", return_value=[]),
            patch("reportlab.pdfbase.pdfmetrics.registerFont"),
            patch("reportlab.pdfbase.ttfonts.TTFont"),
        ):
            result = fr.get_pdf_font()
        assert result == "Times-Roman"


# ---------------------------------------------------------------------------
# get_docx_font — OSError branch
# ---------------------------------------------------------------------------


class TestGetDocxFontOSError:

    def test_os_error_returns_times_new_roman(self):
        mock_path = MagicMock()
        mock_path.exists.side_effect = OSError("disk error")
        with patch.object(fr, "_fonts_dir", return_value=mock_path):
            result = fr.get_docx_font()
        assert result == "Times New Roman"
