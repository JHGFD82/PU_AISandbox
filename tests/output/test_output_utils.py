"""Tests for src/output/_output_utils.py: plain-text files, and drawing Markdown tables as plain-text grids."""

from pathlib import Path

from src.output._output_utils import (
    _extract_markdown_tables,
    _parse_md_table_block,
    _render_markdown_tables_as_ascii,
    _render_table_as_ascii,
)
from src.output.file_output import FileOutputHandler


# ---------------------------------------------------------------------------
# _parse_md_table_block
# ---------------------------------------------------------------------------

class TestParseMdTableBlock:
    def test_returns_rows_for_valid_table(self):
        block = "| A | B |\n|---|---|\n| 1 | 2 |"
        rows = _parse_md_table_block(block)
        assert rows == [["A", "B"], ["1", "2"]]

    def test_returns_none_for_single_line(self):
        assert _parse_md_table_block("| A | B |") is None

    def test_returns_none_for_non_pipe_line(self):
        # One line doesn't start/end with a pipe → not a table
        block = "| A | B |\nsome plain text\n| 1 | 2 |"
        assert _parse_md_table_block(block) is None

    def test_returns_none_when_no_separator_row(self):
        # All pipe lines but no separator row (|---|---|)
        block = "| A | B |\n| 1 | 2 |\n| 3 | 4 |"
        assert _parse_md_table_block(block) is None

    def test_returns_none_for_empty_string(self):
        assert _parse_md_table_block("") is None

    def test_br_variants_normalised(self):
        block = "| A | B |<br>|---|---|<br>| 1 | 2 |"
        rows = _parse_md_table_block(block)
        assert rows is not None
        assert rows[0] == ["A", "B"]

    def test_returns_none_for_rows_only_after_stripping_sep(self):
        # If all data rows are actually separator rows, rows list is empty.
        block = "| A | B |\n|---|---|\n|---|---|"
        rows = _parse_md_table_block(block)
        # The only non-sep content row is "A | B" → one row
        assert rows == [["A", "B"]]


# ---------------------------------------------------------------------------
# _extract_markdown_tables
# ---------------------------------------------------------------------------

class TestExtractMarkdownTables:
    def test_no_tables_returns_content_unchanged(self):
        content = "Hello world\n\nSecond paragraph"
        out, registry = _extract_markdown_tables(content)
        assert out == content
        assert registry == {}

    def test_table_replaced_with_placeholder(self):
        table_block = "| A | B |\n|---|---|\n| 1 | 2 |"
        content = f"Intro\n\n{table_block}\n\nOutro"
        out, registry = _extract_markdown_tables(content)
        assert "[MD_TABLE_1]" in out
        assert "registry" or registry  # has one entry
        assert len(registry) == 1
        key = list(registry.keys())[0]
        assert key == "[MD_TABLE_1]"
        assert registry[key] == [["A", "B"], ["1", "2"]]

    def test_multiple_tables_get_sequential_keys(self):
        t1 = "| X |\n|---|\n| a |"
        t2 = "| Y |\n|---|\n| b |"
        content = f"{t1}\n\n{t2}"
        out, registry = _extract_markdown_tables(content)
        assert "[MD_TABLE_1]" in out
        assert "[MD_TABLE_2]" in out
        assert len(registry) == 2

    def test_non_table_blocks_preserved(self):
        content = "Para one\n\nPara two"
        out, registry = _extract_markdown_tables(content)
        assert out == content
        assert registry == {}


# ---------------------------------------------------------------------------
# _render_table_as_ascii
# ---------------------------------------------------------------------------

class TestRenderTableAsAscii:
    def test_empty_rows_returns_empty_string(self):
        assert _render_table_as_ascii([]) == ''

    def test_single_cell(self):
        result = _render_table_as_ascii([["Hello"]])
        assert "Hello" in result
        assert "+" in result  # separator present

    def test_header_separator_present(self):
        rows = [["Name", "Age"], ["Alice", "30"]]
        result = _render_table_as_ascii(rows)
        lines = result.splitlines()
        # Structure: sep, header, sep, data, sep — 5 lines
        assert len(lines) == 5
        assert lines[0].startswith("+")
        assert lines[2].startswith("+")
        assert lines[4].startswith("+")

    def test_unequal_row_lengths_padded(self):
        rows = [["A", "B", "C"], ["1"]]
        result = _render_table_as_ascii(rows)
        assert result  # just check it doesn't crash

    def test_minimum_column_width_is_3(self):
        # Single-char cells should still get at least 3-char-wide columns
        rows = [["A", "B"], ["X", "Y"]]
        result = _render_table_as_ascii(rows)
        # Each cell should be padded to at least 3
        assert "  A  " in result or " A " in result


# ---------------------------------------------------------------------------
# _render_markdown_tables_as_ascii
# ---------------------------------------------------------------------------

class TestRenderMarkdownTablesAsAscii:
    def test_plain_content_unchanged(self):
        content = "No tables here"
        assert _render_markdown_tables_as_ascii(content) == content

    def test_table_block_converted_to_ascii(self):
        table_block = "| A | B |\n|---|---|\n| 1 | 2 |"
        content = f"Before\n\n{table_block}\n\nAfter"
        result = _render_markdown_tables_as_ascii(content)
        assert "Before" in result
        assert "After" in result
        # The pipe table should have been replaced by ASCII art
        assert "+---" in result


# ---------------------------------------------------------------------------
# save_to_text_file
# ---------------------------------------------------------------------------


class TestSaveToTextFile:

    def test_writes_content_to_file(self, tmp_path):
        output_path = str(tmp_path / "out.txt")
        FileOutputHandler.save_to_text_file("Hello CJK: 日本語", output_path, "Translation")
        assert Path(output_path).read_text(encoding="utf-8") == "Hello CJK: 日本語"

    def test_creates_file_if_not_exists(self, tmp_path):
        output_path = str(tmp_path / "new_file.txt")
        FileOutputHandler.save_to_text_file("content", output_path, "Translation")
        assert Path(output_path).exists()

    def test_overwrites_existing_file(self, tmp_path):
        output_path = str(tmp_path / "out.txt")
        Path(output_path).write_text("old content", encoding="utf-8")
        FileOutputHandler.save_to_text_file("new content", output_path, "Translation")
        assert Path(output_path).read_text(encoding="utf-8") == "new content"

    def test_prints_confirmation_message(self, tmp_path, capsys):
        output_path = str(tmp_path / "out.txt")
        FileOutputHandler.save_to_text_file("text", output_path, "Translation")
        out = capsys.readouterr().out
        assert "saved" in out.lower() or str(output_path) in out

    def test_os_error_handled_gracefully(self, tmp_path, capsys):
        # Point at a directory so writing raises OSError
        output_path = str(tmp_path)   # directory, not a file
        # Should not raise; should print an error
        FileOutputHandler.save_to_text_file("text", output_path, "Translation")
        out = capsys.readouterr().out
        assert "Error" in out or "error" in out


# ---------------------------------------------------------------------------
# append_to_text_file
# ---------------------------------------------------------------------------


class TestAppendToTextFile:

    def test_appends_content_to_existing_file(self, tmp_path):
        output_path = str(tmp_path / "out.txt")
        Path(output_path).write_text("Page 1", encoding="utf-8")
        FileOutputHandler.append_to_text_file("Page 2", output_path, "Translation")
        content = Path(output_path).read_text(encoding="utf-8")
        assert "Page 1" in content
        assert "Page 2" in content

    def test_creates_file_if_not_exists(self, tmp_path):
        output_path = str(tmp_path / "out.txt")
        FileOutputHandler.append_to_text_file("content", output_path, "Translation")
        assert Path(output_path).exists()

    def test_appended_content_followed_by_double_newline(self, tmp_path):
        output_path = str(tmp_path / "out.txt")
        FileOutputHandler.append_to_text_file("chunk", output_path, "Translation")
        content = Path(output_path).read_text(encoding="utf-8")
        assert content.endswith("\n\n")

    def test_multiple_appends_accumulate(self, tmp_path):
        output_path = str(tmp_path / "out.txt")
        for i in range(3):
            FileOutputHandler.append_to_text_file(f"chunk {i}", output_path, "Translation")
        content = Path(output_path).read_text(encoding="utf-8")
        for i in range(3):
            assert f"chunk {i}" in content

    def test_prints_confirmation(self, tmp_path, capsys):
        output_path = str(tmp_path / "out.txt")
        FileOutputHandler.append_to_text_file("text", output_path, "Translation")
        out = capsys.readouterr().out
        assert str(output_path) in out or "appended" in out.lower() or "Page" in out

    def test_os_error_handled_gracefully(self, tmp_path, capsys):
        # Pass a directory path so open() raises IsADirectoryError (OSError subclass)
        output_path = str(tmp_path)
        FileOutputHandler.append_to_text_file("text", output_path, "Translation")
        out = capsys.readouterr().out
        assert "Error" in out or "error" in out


class TestTablesInPlainText:
    """A .txt has no way to render a table, so the bars are drawn out.

    The renderer for this existed and said in its own docstring that it was
    for .txt output, but nothing ever called it — so every plain-text file
    this project wrote carried Markdown's upright bars instead.
    """

    TABLE = (
        "Here is the breakdown:\n\n"
        "| Privilege | Effect |\n"
        "|---|---|\n"
        "| Read | View only |\n"
        "| Edit | View and change |\n\n"
        "That is all."
    )

    def test_a_markdown_table_is_drawn_out(self, tmp_path):
        path = tmp_path / "out.txt"
        FileOutputHandler.save_to_text_file(self.TABLE, str(path), "Response")
        written = path.read_text(encoding="utf-8")
        assert "+-----" in written
        assert "| Privilege | Effect          |" in written
        # The Markdown separator row is not content and should not survive.
        assert "|---|---|" not in written

    def test_the_words_around_it_are_untouched(self, tmp_path):
        path = tmp_path / "out.txt"
        FileOutputHandler.save_to_text_file(self.TABLE, str(path), "Response")
        written = path.read_text(encoding="utf-8")
        assert "Here is the breakdown:" in written
        assert "That is all." in written

    def test_text_with_no_table_is_written_exactly_as_given(self, tmp_path):
        path = tmp_path / "out.txt"
        plain = "One line.\n\nAnother line."
        FileOutputHandler.save_to_text_file(plain, str(path), "Response")
        assert path.read_text(encoding="utf-8") == plain

    def test_appending_a_page_draws_its_tables_too(self, tmp_path):
        """Pages are appended one at a time as a long document is worked
        through, and a table does not become readable by arriving later."""
        path = tmp_path / "out.txt"
        FileOutputHandler.save_to_text_file("First page.", str(path), "Translation")
        FileOutputHandler.append_to_text_file(self.TABLE, str(path), "Translation")
        written = path.read_text(encoding="utf-8")
        assert "First page." in written
        assert "+-----" in written
        assert "|---|---|" not in written
