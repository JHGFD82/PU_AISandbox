"""Tests for ExcelBuilder (save_to_excel)."""

import json
from pathlib import Path

import pytest

from src.output.excel_builder import save_to_excel


openpyxl = pytest.importorskip("openpyxl")


class TestSaveToExcel:

    def test_creates_file(self, tmp_path):
        out = str(tmp_path / "out.xlsx")
        save_to_excel("Hello world", out, label="Test")
        assert Path(out).exists()

    def test_plain_text_goes_to_content_or_text_sheet(self, tmp_path):
        out = str(tmp_path / "plain.xlsx")
        save_to_excel("First line\nSecond line\nThird line", out, label="Test")
        wb = openpyxl.load_workbook(out)
        assert wb.sheetnames  # at least one sheet

    def test_markdown_table_creates_table_sheet(self, tmp_path):
        content = "| Name | Age |\n| --- | --- |\n| Alice | 30 |\n| Bob | 25 |"
        out = str(tmp_path / "table.xlsx")
        save_to_excel(content, out, label="Test")
        wb = openpyxl.load_workbook(out)
        assert any("Table" in name for name in wb.sheetnames)

    def test_markdown_table_header_is_bold(self, tmp_path):
        content = "| Name | Age |\n| --- | --- |\n| Alice | 30 |"
        out = str(tmp_path / "bold.xlsx")
        save_to_excel(content, out, label="Test")
        wb = openpyxl.load_workbook(out)
        table_sheet = next(ws for ws in wb.worksheets if "Table" in ws.title)
        header_cell = table_sheet.cell(row=1, column=1)
        assert header_cell.font.bold

    def test_prose_and_table_together(self, tmp_path):
        content = (
            "Introduction paragraph.\n\n"
            "| A | B |\n| --- | --- |\n| 1 | 2 |\n\n"
            "Conclusion paragraph."
        )
        out = str(tmp_path / "mixed.xlsx")
        save_to_excel(content, out, label="Test")
        wb = openpyxl.load_workbook(out)
        names = wb.sheetnames
        assert any("Table" in n for n in names)
        assert any(n in ("Text", "Content") for n in names)

    def test_fallback_to_txt_when_openpyxl_missing(self, tmp_path):
        import builtins
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "openpyxl":
                raise ImportError("No module named 'openpyxl'")
            return real_import(name, *args, **kwargs)

        out = str(tmp_path / "fallback.xlsx")
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("builtins.__import__", fake_import)
            save_to_excel("Some content", out, label="Test")
        # Fallback writes a .txt file
        assert Path(str(tmp_path / "fallback.txt")).exists()
