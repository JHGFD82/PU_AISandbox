"""Tests for plugins/webui/plugin.py — the plugin contract itself.

Covers requires_professor, subcommand registration/parsing, and run()
dispatch. Does not start a real server (the routes are tested in test_app*.py).
"""

from __future__ import annotations

import argparse
import sys

import pytest

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


class TestTheOtherSubcommands:
    @pytest.mark.parametrize("subcommand,function", [
        ("setup", "_serve_setup"),
        ("set-session-secret", "_generate_session_secret"),
    ])
    def test_each_goes_to_its_own_function(self, monkeypatch, subcommand, function):
        called = []
        replacement = (lambda a: called.append(a)) if function == "_serve_setup" else (lambda: called.append(True))
        monkeypatch.setattr(f"plugins.webui.plugin.{function}", replacement)
        plugin.run(argparse.Namespace(webui_subcommand=subcommand, port=None), None, None, None, None, None)
        assert len(called) == 1


class TestANewSessionSecret:
    @pytest.fixture
    def settings_file(self, tmp_path, monkeypatch):
        import src.settings_store as store

        monkeypatch.setattr(store, "SETTINGS_PATH", tmp_path / "settings.toml")
        return store

    def test_a_long_random_one_is_saved_and_not_shown(self, settings_file, capsys):
        from plugins.webui.plugin import _generate_session_secret

        _generate_session_secret()
        secret = settings_file.get_value("webui.session_secret")
        assert len(secret) >= 40
        out = capsys.readouterr().out
        assert secret not in out and "not shown" in out

    def test_replacing_one_says_everyone_is_signed_out(self, settings_file, capsys):
        from plugins.webui.plugin import _generate_session_secret

        _generate_session_secret()
        first = settings_file.get_value("webui.session_secret")
        capsys.readouterr()
        _generate_session_secret()
        assert settings_file.get_value("webui.session_secret") != first
        assert "signed out" in capsys.readouterr().out


class TestSetupInTheBrowser:
    """A one-page server that asks where the files go, then stops."""

    @pytest.fixture
    def setup_server(self, monkeypatch):
        """The setup page and its server, with a stand-in browser that either answers or walks away."""
        from types import SimpleNamespace

        import uvicorn

        from src import paths

        monkeypatch.setattr(paths, "is_installed", lambda: False)
        seen = SimpleNamespace(answer=None, on_complete=None, config=None)
        app = SimpleNamespace(state=SimpleNamespace())

        def create_setup_app(on_complete):
            seen.on_complete = on_complete
            return app

        class Server:
            def __init__(self, config):
                seen.config = config
                self.should_exit = False

            def run(self):
                if seen.answer is not None:
                    seen.on_complete(seen.answer)

        monkeypatch.setattr(sys.modules["_pu_webui_setup_web"], "create_setup_app", create_setup_app)
        monkeypatch.setattr(uvicorn, "Config", lambda app, **kw: kw)
        monkeypatch.setattr(uvicorn, "Server", Server)
        seen.app = app
        return seen

    def test_it_says_which_folder_was_chosen(self, setup_server, capsys):
        from plugins.webui.plugin import _serve_setup

        setup_server.answer = "/Users/heller/PU_AISandbox_data"
        _serve_setup(argparse.Namespace(port=None))
        assert "Your files are in /Users/heller/PU_AISandbox_data" in capsys.readouterr().out

    def test_it_listens_on_this_computer_only_at_the_port_asked_for(self, setup_server, capsys):
        from plugins.webui.plugin import _serve_setup

        setup_server.answer = "/somewhere"
        _serve_setup(argparse.Namespace(port=8123))
        assert setup_server.config["host"] == "127.0.0.1" and setup_server.config["port"] == 8123
        assert "http://127.0.0.1:8123" in capsys.readouterr().out

    def test_the_server_can_be_stopped_from_the_page(self, setup_server):
        from plugins.webui.plugin import _serve_setup

        setup_server.answer = "/somewhere"
        _serve_setup(argparse.Namespace(port=None))
        assert setup_server.app.state.server is not None

    def test_closing_the_browser_without_answering_says_how_to_start_again(self, setup_server):
        from plugins.webui.plugin import _serve_setup

        with pytest.raises(CLIError, match="Setup was not finished"):
            _serve_setup(argparse.Namespace(port=None))

    def test_a_copy_already_set_up_is_left_alone(self, setup_server, monkeypatch, capsys):
        from plugins.webui.plugin import _serve_setup
        from src import paths

        monkeypatch.setattr(paths, "is_installed", lambda: True)
        _serve_setup(argparse.Namespace(port=None))
        assert "already set up" in capsys.readouterr().out
        assert setup_server.config is None
