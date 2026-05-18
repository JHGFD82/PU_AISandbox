"""Tests for JsonProcessor."""

import json
import os

import pytest

from src.processors.json_processor import JsonProcessor, _flatten_value


class TestFlattenValue:

    def test_flat_dict(self):
        lines = _flatten_value({"a": 1, "b": "hello"})
        assert any("a: 1" in l for l in lines)
        assert any("b: hello" in l for l in lines)

    def test_nested_dict(self):
        lines = _flatten_value({"outer": {"inner": "value"}})
        assert any("outer" in l for l in lines)
        assert any("inner: value" in l for l in lines)

    def test_list_of_primitives(self):
        lines = _flatten_value([10, 20, 30])
        assert any("[0]: 10" in l for l in lines)

    def test_list_of_dicts(self):
        lines = _flatten_value([{"x": 1}])
        assert any("[0]" in l for l in lines)
        assert any("x: 1" in l for l in lines)

    def test_primitive_value(self):
        lines = _flatten_value("hello")
        assert lines == ["hello"]

    def test_empty_dict(self):
        lines = _flatten_value({})
        assert lines == []


class TestJsonProcessor:

    def test_simple_json_file(self, tmp_path):
        data = {"name": "Alice", "age": 30}
        p = tmp_path / "test.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        pages = JsonProcessor.process_json_with_pages(str(p))
        combined = "\n".join(pages)
        assert "Alice" in combined
        assert "age" in combined

    def test_returns_list_of_strings(self, tmp_path):
        p = tmp_path / "data.json"
        p.write_text('{"key": "value"}', encoding="utf-8")
        pages = JsonProcessor.process_json_with_pages(str(p))
        assert isinstance(pages, list)
        assert all(isinstance(x, str) for x in pages)

    def test_empty_json_object_returns_empty_page(self, tmp_path):
        p = tmp_path / "empty.json"
        p.write_text("{}", encoding="utf-8")
        pages = JsonProcessor.process_json_with_pages(str(p))
        assert pages == [""]

    def test_list_json(self, tmp_path):
        data = [{"a": 1}, {"b": 2}]
        p = tmp_path / "list.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        pages = JsonProcessor.process_json_with_pages(str(p))
        combined = "\n".join(pages)
        assert "a: 1" in combined

    def test_invalid_json_raises_exception(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("this is not json", encoding="utf-8")
        with pytest.raises(Exception, match="Failed to parse"):
            JsonProcessor.process_json_with_pages(str(p))

    def test_missing_file_raises_exception(self, tmp_path):
        missing = str(tmp_path / "nonexistent.json")
        with pytest.raises(Exception, match="Failed to read"):
            JsonProcessor.process_json_with_pages(missing)

    def test_respects_page_size(self, tmp_path):
        data = {f"key{i}": f"value{i}" for i in range(50)}
        p = tmp_path / "big.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        pages_small = JsonProcessor.process_json_with_pages(str(p), target_page_size=100)
        pages_large = JsonProcessor.process_json_with_pages(str(p), target_page_size=10000)
        assert len(pages_small) >= len(pages_large)
