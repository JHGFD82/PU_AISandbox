"""Tests for src/runtime/plugin.py's register_plugin_module(): filing a plugin's own files where core looks for them."""

import sys

import pytest

from src.runtime.plugin import register_plugin_module


@pytest.fixture
def plugin_dir(tmp_path, monkeypatch):
    """A plugin folder with one service file in it, and nothing left filed afterwards."""
    services = tmp_path / "src" / "services"
    services.mkdir(parents=True)
    (services / "demo_service.py").write_text("WHICH = 'the plugin'\n", encoding="utf-8")
    (services / "other_service.py").write_text("WHICH = 'the extension'\n", encoding="utf-8")
    for name in ("src.services.demo_service", "pu_plugin.demo.settings", "_pu_demo_flat"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    yield tmp_path
    import src.services
    for name in ("src.services.demo_service", "pu_plugin.demo.settings", "_pu_demo_flat"):
        sys.modules.pop(name, None)
    src.services.__dict__.pop("demo_service", None)


class TestFilingAModule:
    def test_it_is_importable_under_the_name_given(self, plugin_dir):
        register_plugin_module(plugin_dir, "src.services.demo_service", "src/services/demo_service.py")
        from src.services import demo_service
        assert demo_service.WHICH == "the plugin"

    def test_it_can_be_reached_attribute_by_attribute(self, plugin_dir):
        """How monkeypatch.setattr("src.services.demo_service.X", ...) finds it."""
        import src.services

        register_plugin_module(plugin_dir, "src.services.demo_service", "src/services/demo_service.py")
        assert src.services.demo_service is sys.modules["src.services.demo_service"]

    def test_a_made_up_parent_is_no_trouble(self, plugin_dir):
        register_plugin_module(plugin_dir, "pu_plugin.demo.settings", "src/services/demo_service.py")
        assert sys.modules["pu_plugin.demo.settings"].WHICH == "the plugin"

    def test_a_name_with_no_parent_at_all_is_no_trouble(self, plugin_dir):
        register_plugin_module(plugin_dir, "_pu_demo_flat", "src/services/demo_service.py")
        assert sys.modules["_pu_demo_flat"].WHICH == "the plugin"

    def test_a_file_that_is_not_there_is_skipped(self, plugin_dir):
        register_plugin_module(plugin_dir, "src.services.demo_service", "src/services/missing.py")
        assert "src.services.demo_service" not in sys.modules


class TestWhenTheNameIsTaken:
    def test_what_is_there_is_left_alone(self, plugin_dir):
        register_plugin_module(plugin_dir, "src.services.demo_service", "src/services/demo_service.py")
        register_plugin_module(plugin_dir, "src.services.demo_service", "src/services/other_service.py")
        assert sys.modules["src.services.demo_service"].WHICH == "the plugin"

    def test_unless_the_plugin_means_to_replace_it(self, plugin_dir):
        """An extension supplying its own version of another plugin's module."""
        register_plugin_module(plugin_dir, "src.services.demo_service", "src/services/demo_service.py")
        register_plugin_module(plugin_dir, "src.services.demo_service", "src/services/other_service.py",
                               override=True)
        import src.services
        assert sys.modules["src.services.demo_service"].WHICH == "the extension"
        assert src.services.demo_service.WHICH == "the extension"
