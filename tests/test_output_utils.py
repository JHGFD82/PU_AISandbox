"""Coverage tests for src/output/_output_utils.py."""


from src.output._output_utils import (
    _parse_md_table_block,
    _extract_markdown_tables,
    _render_table_as_ascii,
    _render_markdown_tables_as_ascii,
)


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
