"""Tests for JsonBuilder (save_to_json)."""

import json
from pathlib import Path

import pytest

from src.output.json_builder import save_to_json


class TestSaveToJson:

    def test_creates_file(self, tmp_path):
        out = str(tmp_path / "out.json")
        save_to_json("Hello world", out, label="Test")
        assert Path(out).exists()

    def test_plain_text_wrapped_as_content_key(self, tmp_path):
        out = str(tmp_path / "wrapped.json")
        save_to_json("some plain text", out, label="Test")
        data = json.loads(Path(out).read_text(encoding="utf-8"))
        assert data == {"content": "some plain text"}

    def test_valid_json_is_round_tripped(self, tmp_path):
        original = {"key": "value", "number": 42}
        out = str(tmp_path / "passthrough.json")
        save_to_json(json.dumps(original), out, label="Test")
        data = json.loads(Path(out).read_text(encoding="utf-8"))
        assert data == original

    def test_output_is_pretty_printed(self, tmp_path):
        out = str(tmp_path / "pretty.json")
        save_to_json('{"a":1}', out, label="Test")
        raw = Path(out).read_text(encoding="utf-8")
        assert "\n" in raw  # pretty-printed JSON has newlines

    def test_unicode_preserved(self, tmp_path):
        out = str(tmp_path / "unicode.json")
        save_to_json("こんにちは世界", out, label="Test")
        data = json.loads(Path(out).read_text(encoding="utf-8"))
        assert data["content"] == "こんにちは世界"

    def test_trailing_newline(self, tmp_path):
        out = str(tmp_path / "newline.json")
        save_to_json("{}", out, label="Test")
        raw = Path(out).read_text(encoding="utf-8")
        assert raw.endswith("\n")

    def test_write_error_raises(self, tmp_path):
        # Point to a directory, not a file, to trigger OSError.
        with pytest.raises(OSError):
            save_to_json("content", str(tmp_path), label="Test")
