"""Pytest set-up for the web interface plugin's tests.

Files every module this plugin registers under the name its code imports it
by (see tests/plugin_modules.py). Most use flat, dot-free names such as
``_pu_webui_app`` rather than a dotted ``pu_plugin.webui.*`` one — see
``plugins/webui/src/app.py``'s module docstring for why.
"""

import sys
from functools import partial
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import src.settings as core_settings_mod
import src.settings_store as settings_store_mod
from src.config import load_professor_config as _real_load_professor_config
from tests.plugin_modules import register

_register = partial(register, Path(__file__).resolve().parent.parent)


# Same dependency order plugin.py itself uses.
_register("pu_plugin.webui.settings", "src/settings.py")
_register("_pu_webui_file_picker", "src/file_picker.py")
_register("_pu_webui_auth", "src/auth.py")
_register("_pu_webui_conversation", "src/conversation.py")
_register("_pu_webui_attachments", "src/attachments.py")
_register("_pu_webui_export", "src/export.py")
_register("_pu_webui_jobs", "src/jobs.py")
_register("src.services.chat_service", "src/services/chat_service.py")
_register("_pu_webui_git_tool", "src/git_tool.py")
_register("_pu_webui_plugin_install", "src/plugin_install.py")
_register("_pu_webui_upgrade", "src/upgrade.py")
_register("_pu_webui_branding", "src/branding.py")
_register("_pu_webui_stopping", "src/stopping.py")
_register("_pu_webui_setup_web", "src/setup_web.py")
_register("_pu_webui_app", "src/app.py")

@pytest.fixture(autouse=True)
def _do_not_look_for_updates_while_testing():
    """Stop every create_app() in the suite reaching out to GitHub.

    Starting the server begins looking for a newer version on a background
    thread. That is right in a running sandbox and wrong in a test run: it
    would be a real `git fetch` against the real repository, once per test
    that builds an app, and the answers would depend on what had been pushed
    that morning.

    Only the looking-at-startup is stopped. upgrade.check_for_updates itself is
    left alone, because test_upgrade.py runs the real thing against
    repositories it makes for itself, and the update routes' own tests put
    their own answer in place.
    """
    app_module = sys.modules["_pu_webui_app"]
    before = app_module._look_for_an_update_in_the_background
    app_module._look_for_an_update_in_the_background = lambda: None
    yield
    app_module._look_for_an_update_in_the_background = before


# Also import the real plugin.py module (not just the src/*.py files above,
# which conftest registers directly to avoid needing the full plugin loader).
# plugin.py's own _register() calls are no-ops for anything already
# registered above, but its module-level register_env_field() calls for
# webui.passphrase_hash/webui.session_secret only run once this actually
# imports — without this, tests that depend on those two paths being known
# (e.g. the /api/settings routes' "which paths are directly editable" check)
# would only pass if some other test file happened to import plugin.py
# first, which depends on alphabetical test-file collection order rather
# than being reliably true.
import plugins.webui.plugin  # noqa: F401

# ── Shared by the tests of app.py and of the pages ───────────────────────────
# _configured_professors and _no_passphrase are not applied automatically:
# this conftest covers every test in the plugin, and only the app's and pages'
# tests want them. Those files ask for them with
# ``pytestmark = pytest.mark.usefixtures("_configured_professors", "_no_passphrase")``.


@pytest.fixture
def _configured_professors(monkeypatch):
    """Two fake professors, matching the {safe_name: {...}} shape load_professor_config() returns."""
    fake_config = {
        "heller": {"name": "Heller", "key": "sk-heller", "backup_key": None, "safe_name": "heller"},
        "smith": {"name": "Smith", "key": "sk-smith", "backup_key": None, "safe_name": "smith"},
    }
    # app.py imported load_professor_config into its own namespace at import
    # time, so it must be patched there (on the actual registered module
    # object, "_pu_webui_app" — see conftest.py) rather than on src.config,
    # which every other route would still see the real version of.
    monkeypatch.setattr(sys.modules["_pu_webui_app"], "load_professor_config", lambda: fake_config)
    return fake_config


@pytest.fixture
def _no_passphrase(monkeypatch):
    """Default every test to the open-access (no passphrase configured) case."""
    auth = sys.modules["_pu_webui_auth"]
    app_module = sys.modules["_pu_webui_app"]
    monkeypatch.setattr(app_module, "_auth_backend", auth.PassphraseBackend(passphrase_hash=""))


@pytest.fixture
def client(tmp_path, monkeypatch):
    app_module = sys.modules["_pu_webui_app"]
    conversation = sys.modules["_pu_webui_conversation"]
    jobs = sys.modules["_pu_webui_jobs"]
    # Redirect conversation storage to a temp dir so tests never touch real data/.
    monkeypatch.setattr(conversation, "CONVERSATIONS_DIR", tmp_path / "conversations")
    # jobs.py works out the same directory independently, so patching only the
    # one above left every job test writing its output into the real
    # data/conversations/<netid>/_job_outputs/ — which is how a folder for a
    # test-fixture professor kept reappearing in real data after it had been
    # migrated away. Both have to point at the temp directory.
    monkeypatch.setattr(jobs, "_CONVERSATIONS_DIR", tmp_path / "conversations")

    app = app_module.create_app()
    # A loopback client address, because that's what a browser on this same
    # computer looks like — and /api/pick-path refuses anything else.
    return TestClient(app, client=("127.0.0.1", 50000))


@pytest.fixture
def preference_file(tmp_path, monkeypatch):
    """A preferences.toml this test owns, with the real one out of reach.

    Two places get pointed at it, because they are two different things: a
    route writes through src.paths, and the settings layer reads a path it
    worked out when it was first imported. In a running sandbox they are the
    same file; here they both have to be told about this one.
    """
    path = tmp_path / "preferences.toml"
    path.write_text("[webui]\n")
    import src.paths
    monkeypatch.setattr(src.paths, "preferences_path", lambda: path)
    monkeypatch.setattr(core_settings_mod, "_PREFERENCES_PATH", path)
    return path


@pytest.fixture
def unlocked_client(client):
    resp = client.post("/unlock", data={"passphrase": ""})
    assert resp.status_code in (200, 303)
    return client


@pytest.fixture
def settings_env(monkeypatch, tmp_path):
    """Redirect settings.toml to a tmp file and restore the real, settings_store-backed
    load_professor_config for these tests — undoing the module-level
    _configured_professors fixture's fixed fake dict, since these tests need to
    see data actually persisted through src/settings_store.py, not a stub."""
    monkeypatch.setattr(settings_store_mod, "SETTINGS_PATH", tmp_path / "settings.toml")
    app_module = sys.modules["_pu_webui_app"]
    monkeypatch.setattr(app_module, "load_professor_config", _real_load_professor_config)
    return tmp_path


@pytest.fixture
def a_catalog(monkeypatch, tmp_path):
    """A small real catalog on disk, read through the ordinary path.

    One model tested and able to read images, one recorded as text-only by the
    old assumption with nothing to show it was ever asked.
    """
    import json

    import src.models.catalog as catalog_module

    path = tmp_path / "model_catalog.json"
    path.write_text(json.dumps({
        "config": {"pricing_unit": 1_000_000, "monthly_limit": 250.0},
        "models": {
            "gpt-4o": {"input": 2.5, "output": 10.0, "supports_vision": True,
                       "portkey_id": "openai/gpt-4o",
                       "rejects": {"top_p": "tested on add: refused"}},
            "old-text-model": {"input": 1.0, "output": 2.0, "supports_vision": False,
                               "portkey_id": "openai/old-text-model"},
        },
    }))
    monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: path)
    return path
