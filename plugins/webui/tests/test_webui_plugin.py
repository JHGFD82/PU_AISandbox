"""Tests for plugins/webui/plugin.py — the plugin contract itself.

Covers requires_professor, subcommand registration/parsing, and run()
dispatch. Does not start a real server (see test_app.py for route behavior).

Named test_webui_plugin.py rather than test_plugin.py because pytest's
default (prepend) import mode identifies test modules by bare basename when
there's no __init__.py in the tests/ directory — plugins/prompt/tests/
already has a test_plugin.py, and reusing that name here causes a real
collection-time error ("import file mismatch"), not just a style nit.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

# Ensure the plugin module can be imported (repo root must be on sys.path) —
# same defensive pattern as plugins/prompt/tests/test_plugin.py.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.webui.plugin import WebUiPlugin, plugin
from src.errors import CLIError


class TestPluginIdentity:
    def test_commands(self):
        assert plugin.commands == ["webui"]

    def test_requires_professor_is_false(self):
        assert plugin.requires_professor is False

    def test_module_level_instance_is_websuiplugin(self):
        assert isinstance(plugin, WebUiPlugin)


class TestRegisterSubparsers:
    @pytest.fixture
    def parser(self):
        p = argparse.ArgumentParser()
        subparsers = p.add_subparsers(dest="command")
        plugin.register_subparsers(subparsers)
        return p

    def test_serve_defaults(self, parser):
        args = parser.parse_args(["webui", "serve"])
        assert args.command == "webui"
        assert args.webui_subcommand == "serve"
        assert args.host is None
        assert args.port is None

    def test_serve_with_host_and_port(self, parser):
        args = parser.parse_args(["webui", "serve", "--host", "0.0.0.0", "--port", "9000"])
        assert args.host == "0.0.0.0"
        assert args.port == 9000

    def test_set_passphrase(self, parser):
        args = parser.parse_args(["webui", "set-passphrase"])
        assert args.webui_subcommand == "set-passphrase"


class TestRun:
    def test_no_subcommand_raises_cli_error(self):
        args = argparse.Namespace(webui_subcommand=None)
        with pytest.raises(CLIError):
            plugin.run(args, None, None, None, None, None)

    def test_serve_dispatches_to_serve(self, monkeypatch):
        called = {}
        monkeypatch.setattr("plugins.webui.plugin._serve", lambda a: called.setdefault("serve", a))
        args = argparse.Namespace(webui_subcommand="serve", host=None, port=None)
        plugin.run(args, None, None, None, None, None)
        assert "serve" in called

    def test_set_passphrase_dispatches(self, monkeypatch):
        called = {}
        monkeypatch.setattr(
            "plugins.webui.plugin._print_passphrase_hash", lambda: called.setdefault("called", True)
        )
        args = argparse.Namespace(webui_subcommand="set-passphrase")
        plugin.run(args, None, None, None, None, None)
        assert called.get("called") is True

    def test_professor_is_ignored(self, monkeypatch):
        """run() must not require or validate professor — it's always None for this plugin."""
        monkeypatch.setattr("plugins.webui.plugin._serve", lambda a: None)
        args = argparse.Namespace(webui_subcommand="serve", host=None, port=None)
        # Should not raise even though professor is explicitly None.
        plugin.run(args, None, "gpt-4o", 0.5, 0.9, 1000)


class TestPrintPassphraseHash:
    def test_writes_hash_to_settings(self, monkeypatch, capsys):
        inputs = iter(["hunter2", "hunter2"])
        monkeypatch.setattr("getpass.getpass", lambda *_: next(inputs))
        written = {}
        monkeypatch.setattr(
            "src.settings_store.set_value",
            lambda path, value: written.setdefault(path, value),
        )
        from plugins.webui.plugin import _print_passphrase_hash
        _print_passphrase_hash()
        out = capsys.readouterr().out
        assert written.get("webui.passphrase_hash")
        assert "Unlock passphrase set" in out

    def test_mismatched_passphrases_raises(self, monkeypatch):
        inputs = iter(["hunter2", "different"])
        monkeypatch.setattr("getpass.getpass", lambda *_: next(inputs))
        from plugins.webui.plugin import _print_passphrase_hash
        with pytest.raises(CLIError, match="did not match"):
            _print_passphrase_hash()

    def test_empty_passphrase_raises(self, monkeypatch):
        inputs = iter(["", ""])
        monkeypatch.setattr("getpass.getpass", lambda *_: next(inputs))
        from plugins.webui.plugin import _print_passphrase_hash
        with pytest.raises(CLIError, match="cannot be empty"):
            _print_passphrase_hash()


class TestServeBindGuard:
    """`webui serve` must not publish an ungated interface to the network.

    With no passphrase set the interface has no gate at all, which is a fine
    default while it's reachable only from this computer. Combined with a
    host other machines can reach, it exposes every professor's
    conversations, spending data, and API budget to anyone on the network.
    """

    @staticmethod
    def _args(host):
        return argparse.Namespace(host=host, port=8000)

    @pytest.fixture
    def no_passphrase(self, monkeypatch):
        import plugins.webui.src.auth as auth_mod
        monkeypatch.setattr(
            auth_mod, "get_configured_backend", lambda: auth_mod.PassphraseBackend(passphrase_hash="")
        )
        return auth_mod

    @pytest.fixture
    def with_passphrase(self, monkeypatch):
        import plugins.webui.src.auth as auth_mod
        monkeypatch.setattr(
            auth_mod,
            "get_configured_backend",
            lambda: auth_mod.PassphraseBackend(passphrase_hash=auth_mod.hash_passphrase("secret")),
        )
        return auth_mod

    @pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.50", "::"])
    def test_refuses_public_host_without_passphrase(self, host, no_passphrase, monkeypatch):
        import plugins.webui.plugin as plugin_mod
        monkeypatch.setitem(sys.modules, "_pu_webui_auth", no_passphrase)
        with pytest.raises(CLIError) as exc:
            plugin_mod._serve(self._args(host))
        assert "set-passphrase" in str(exc.value)
        assert host in str(exc.value)

    @pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "LocalHost"])
    def test_allows_loopback_without_passphrase(self, host, no_passphrase, monkeypatch):
        """The painless local default must keep working untouched."""
        import plugins.webui.plugin as plugin_mod
        started = {}
        monkeypatch.setitem(sys.modules, "_pu_webui_auth", no_passphrase)
        monkeypatch.setitem(
            sys.modules, "_pu_webui_app",
            type("M", (), {"run_server": staticmethod(lambda **kw: started.update(kw))})(),
        )
        plugin_mod._serve(self._args(host))
        assert started == {"host": host, "port": 8000}

    def test_allows_public_host_once_a_passphrase_is_set(self, with_passphrase, monkeypatch):
        import plugins.webui.plugin as plugin_mod
        started = {}
        monkeypatch.setitem(sys.modules, "_pu_webui_auth", with_passphrase)
        monkeypatch.setitem(
            sys.modules, "_pu_webui_app",
            type("M", (), {"run_server": staticmethod(lambda **kw: started.update(kw))})(),
        )
        plugin_mod._serve(self._args("0.0.0.0"))
        assert started == {"host": "0.0.0.0", "port": 8000}
