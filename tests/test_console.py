"""Tests for src/console.py: the headings and rules the command line prints around results."""

from src.console import (
    print_section,
    print_banner,
    print_subsection,
    print_pass_result,
)


class TestPrintSection:
    def test_outputs_title_and_content(self, capsys):
        print_section("Translation", "Hello world")
        out = capsys.readouterr().out
        assert "Translation" in out
        assert "Hello world" in out
        assert "===" in out


class TestPrintBanner:
    def test_outputs_title_between_lines(self, capsys):
        print_banner("TOKEN REPORT")
        out = capsys.readouterr().out
        assert "TOKEN REPORT" in out
        assert "=" * 10 in out

    def test_custom_width(self, capsys):
        print_banner("TITLE", width=30)
        out = capsys.readouterr().out
        assert "=" * 30 in out


class TestPrintSubsection:
    def test_outputs_label_and_rule(self, capsys):
        print_subsection("Model Breakdown")
        out = capsys.readouterr().out
        assert "Model Breakdown" in out
        assert "---" in out


class TestPrintPassResult:
    def test_outputs_label_and_content(self, capsys):
        print_pass_result("Pass 1/3 result", "transcribed text")
        out = capsys.readouterr().out
        assert "Pass 1/3 result" in out
        assert "transcribed text" in out
        assert "---" in out

    def test_outputs_empty_content_without_error(self, capsys):
        print_pass_result("label", "")
        out = capsys.readouterr().out
        assert "label" in out
