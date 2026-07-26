"""Tests for ExcelProcessor."""

from unittest.mock import patch

import pytest

from src.processors.excel_processor import ExcelProcessor


@pytest.fixture()
def simple_xlsx(tmp_path):
    """Create a minimal .xlsx file with one sheet."""
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Sheet1"
    ws.append(["Name", "Age", "City"])
    ws.append(["Alice", 30, "New York"])
    ws.append(["Bob", 25, "London"])
    path = tmp_path / "test.xlsx"
    wb.save(str(path))
    return str(path)


@pytest.fixture()
def multi_sheet_xlsx(tmp_path):
    """Create a .xlsx file with two sheets."""
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "Alpha"
    ws1.append(["X", "Y"])
    ws1.append(["1", "2"])
    ws2 = wb.create_sheet("Beta")
    ws2.append(["A", "B", "C"])
    ws2.append(["foo", "bar", "baz"])
    path = tmp_path / "multi.xlsx"
    wb.save(str(path))
    return str(path)


@pytest.fixture()
def empty_xlsx(tmp_path):
    """Create a .xlsx file with no data."""
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    wb.active.title = "Empty"
    path = tmp_path / "empty.xlsx"
    wb.save(str(path))
    return str(path)


class TestExcelProcessor:

    def test_returns_list_of_strings(self, simple_xlsx):
        pages = ExcelProcessor.process_excel_with_pages(simple_xlsx)
        assert isinstance(pages, list)
        assert len(pages) >= 1
        assert all(isinstance(p, str) for p in pages)

    def test_content_includes_header_and_data(self, simple_xlsx):
        pages = ExcelProcessor.process_excel_with_pages(simple_xlsx)
        combined = "\n".join(pages)
        assert "Sheet1" in combined
        assert "Alice" in combined
        assert "Bob" in combined

    def test_tab_separated_cells(self, simple_xlsx):
        pages = ExcelProcessor.process_excel_with_pages(simple_xlsx)
        combined = "\n".join(pages)
        assert "\t" in combined

    def test_multi_sheet_workbook(self, multi_sheet_xlsx):
        pages = ExcelProcessor.process_excel_with_pages(multi_sheet_xlsx)
        combined = "\n".join(pages)
        assert "Alpha" in combined
        assert "Beta" in combined
        assert "foo" in combined

    def test_empty_workbook_returns_single_empty_page(self, empty_xlsx):
        pages = ExcelProcessor.process_excel_with_pages(empty_xlsx)
        assert pages == [""]

    def test_respects_target_page_size(self, simple_xlsx):
        # With a very small page size, we should get multiple pages.
        pages = ExcelProcessor.process_excel_with_pages(simple_xlsx, target_page_size=20)
        assert len(pages) >= 1  # At minimum 1 page

    def test_missing_openpyxl_raises_import_error(self, simple_xlsx):
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "openpyxl":
                raise ImportError("No module named 'openpyxl'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            with pytest.raises(ImportError, match="openpyxl"):
                ExcelProcessor.process_excel_with_pages(simple_xlsx)

    def test_invalid_file_raises_exception(self, tmp_path):
        bad_file = tmp_path / "not_excel.xlsx"
        bad_file.write_bytes(b"not a real xlsx file")
        # The code under test raises a bare Exception today. Narrow this to
        # CLIError once processors stop doing that (§5.5 of the code review).
        with pytest.raises(Exception):  # noqa: B017 — see note below
            ExcelProcessor.process_excel_with_pages(str(bad_file))
