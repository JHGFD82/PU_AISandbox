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
