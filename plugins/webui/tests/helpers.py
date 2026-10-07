"""What the web interface's tests share: where its files are, and small helpers for its pages and routes.

Imported as ``from plugins.webui.tests.helpers import ...``. The fixtures they
go with (``client``, ``unlocked_client`` and others) are in this folder's
conftest.py.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# The plugin's own src/ folder, and the repository it sits in — fixed here so
# that a test file's own depth in tests/ never matters.


WEBUI_SRC = Path(__file__).resolve().parents[1] / "src"
REPO_ROOT = Path(__file__).resolve().parents[3]


def _rendered_template(name: str) -> str:
    """Any template as the browser receives it, with every {% include %} resolved."""
    from fastapi.templating import Jinja2Templates

    directory = WEBUI_SRC / "templates"
    return Jinja2Templates(directory=str(directory)).get_template(name).render(request=None)


def _rendered_chat() -> str:
    """chat.html as the browser receives it, with every {% include %} resolved.

    Reading the file alone stopped being the same thing once the combobox moved
    into a partial the page includes.
    """
    from fastapi.templating import Jinja2Templates

    directory = WEBUI_SRC / "templates"
    return Jinja2Templates(directory=str(directory)).get_template("chat.html").render(request=None)


def _parse_sse(text: str) -> list[dict]:
    """Turn a raw "data: {...}\\n\\n" SSE response body into a list of parsed event dicts."""
    events = []
    for block in text.strip().split("\n\n"):
        for line in block.strip().split("\n"):
            if line.startswith("data:"):
                events.append(json.loads(line[len("data:"):].strip()))
    return events


def _fake_plugin(action_id="translate", run_ui_action=None, preview_ui_action=None):
    """A minimal stand-in for a plugin declaring ui_action + run_ui_action —
    mirrors plugins/webui/tests/test_jobs.py's _FakePlugin, duplicated here
    (rather than imported) since this module doesn't otherwise depend on
    that test file.

    ``preview_ui_action`` is only attached as a real method when a callable
    is passed in — matching the optional, ``hasattr``-checked contract
    described in src/runtime/plugin.py, so a test can also exercise the
    "this plugin doesn't implement a preview" path.
    """
    from src.runtime.ui_action import UiAction

    class _Plugin:
        def __init__(self):
            self.ui_action = UiAction(id=action_id, label="Fake action", command=action_id)

        def run_ui_action(self, fields, professor, model, on_progress, output_dir, on_page_text=None):
            return run_ui_action(fields, professor, model, on_progress, output_dir)

    plugin = _Plugin()
    if preview_ui_action is not None:
        plugin.preview_ui_action = lambda fields, professor, model: preview_ui_action(fields, professor, model)
    return plugin


def _wait_for_job_done(client, conversation_id, professor, timeout=2.0):
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        conv = client.get(
            f"/api/conversations/{conversation_id}", params={"professor": professor}
        ).json()
        if conv["active_job_id"] is None:
            return conv
        time.sleep(0.01)
    pytest.fail("Job did not finish within timeout.")


def _fake_ui_job_result(output_dir):
    from src.runtime.ui_action import UiJobResult
    out = f"{output_dir}/out.txt"
    with open(out, "w", encoding="utf-8") as f:
        f.write("done")
    return UiJobResult(output_path=out, output_filename="out.txt", summary="Done.")


def app_module_threading():
    """The threading module app.py schedules its restarts through."""
    return sys.modules["_pu_webui_app"].threading


def _code_only(text: str) -> str:
    """Return *text* with its line comments taken out.

    These tests ask what the page does, and a comment saying why it does not
    do something else is not the page doing it. Without this, explaining in a
    comment that innerHTML is the wrong choice reads as having used it.
    """
    return "\n".join(line.split("//")[0] for line in text.splitlines())
