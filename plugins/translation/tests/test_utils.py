"""Tests for plugins/translation/utils.py — validate_page_nums."""

import argparse

import pytest

from plugins.translation.utils import validate_page_nums


class TestValidatePageNums:

    def test_single_page_number(self):
        assert validate_page_nums("5") == "5"

    def test_page_range(self):
        assert validate_page_nums("1-10") == "1-10"

    def test_first_page(self):
        assert validate_page_nums("1") == "1"

    def test_comma_separated_pages_valid(self):
        assert validate_page_nums("1,2") == "1,2"

    def test_multi_range_valid(self):
        assert validate_page_nums("4,15-17,20,30-55") == "4,15-17,20,30-55"

    def test_multi_range_with_spaces_valid(self):
        assert validate_page_nums("4, 15-17, 20") == "4, 15-17, 20"

    def test_letters_rejected(self):
        with pytest.raises(argparse.ArgumentTypeError):
            validate_page_nums("abc")

    def test_double_range_rejected(self):
        with pytest.raises(argparse.ArgumentTypeError):
            validate_page_nums("1-2-3")

    def test_empty_string_rejected(self):
        with pytest.raises(argparse.ArgumentTypeError):
            validate_page_nums("")

    def test_space_without_comma_rejected(self):
        with pytest.raises(argparse.ArgumentTypeError):
            validate_page_nums("1 2")
