"""First-time setup, in a browser. The route ``start.py`` takes.

The same questions ``src/setup_prompts.py`` asks at the command line, asked
as a web page instead. Both are only ever a way of putting the questions:
the answers go to ``src/first_run.py``, which is the one place that decides
anything. Neither can drift from the other about what counts as an existing
setup, or about what must never be overwritten.

Neither route is the lesser one. Someone working at the command line has
``python main.py settings setup``; someone who ran ``start.py`` gets this,
without being asked to choose between them first.

This runs as its own short-lived server, separate from the sandbox's real
web interface, and stops as soon as setup is done. That separation is not
tidiness — it is the same reason the command line tells you to run your
command again after setting up. Several modules work out where the settings
live *as they are imported*, so a server started before setup would be
holding stale answers afterwards. Finishing and starting fresh means nothing
is ever half-configured.

Registered into ``sys.modules`` as ``_pu_webui_setup_web``, the same flat
naming every other file in this plugin uses — see ``app.py``'s module
docstring for why.
"""

from __future__ import annotations

import html
import sys
import threading
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from src import first_run, paths

file_picker = sys.modules["_pu_webui_file_picker"]


class PickFolderBody(BaseModel):
    """What the "Browse…" button sends: where the chooser should open."""

    start: str | None = None

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Set up the PU AI Sandbox</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{
    font: 16px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    max-width: 40rem; margin: 3rem auto; padding: 0 1.25rem;
  }}
  h1 {{ font-size: 1.5rem; margin-bottom: .25rem; }}
  .lede {{ color: #666; margin-top: 0; }}
  fieldset {{ border: 1px solid #ccc; border-radius: 8px; padding: 1rem 1.25rem; margin: 1.5rem 0; }}
  legend {{ padding: 0 .4rem; font-weight: 600; }}
  label {{ display: block; margin: .75rem 0 .25rem; }}
  input[type=text] {{ width: 100%; padding: .55rem; font: inherit; border: 1px solid #999; border-radius: 6px; }}
  .found {{ background: #f3f7ff; border-color: #9db8e8; }}
  .note {{ background: #fff8e6; border: 1px solid #e0c169; border-radius: 8px; padding: .9rem 1.1rem; margin: 1.25rem 0; }}
  .error {{ background: #ffeaea; border: 1px solid #d98080; border-radius: 8px; padding: .9rem 1.1rem; margin: 1.25rem 0; }}
  button {{ font: inherit; padding: .6rem 1.4rem; border-radius: 6px; border: 0; background: #2b6cb0; color: #fff; cursor: pointer; }}
  button:disabled {{ opacity: .55; cursor: default; }}
  button.browse, button.secondary {{ background: transparent; color: inherit; border: 1px solid #999; padding: .5rem 1rem; }}
  .field-row {{ display: flex; gap: .5rem; align-items: center; }}
  .field-row input[type=text] {{ flex: 1; }}
  details {{ margin: 1.5rem 0; }}
  details summary {{ cursor: pointer; color: #2b6cb0; }}
  @media (prefers-color-scheme: dark) {{ details summary {{ color: #7aa7dd; }} }}
  ul {{ margin: .4rem 0; padding-left: 1.2rem; }}
  code {{ background: rgba(128,128,128,.15); padding: .1rem .3rem; border-radius: 4px; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #14171a; color: #e6e6e6; }}
    .lede {{ color: #9aa4ad; }}
    fieldset {{ border-color: #333; }}
    input[type=text] {{ background: #1d2126; color: #e6e6e6; border-color: #444; }}
    .found {{ background: #16202e; border-color: #2f4a72; }}
    .note {{ background: #2a2412; border-color: #6b5a25; }}
    .error {{ background: #2c1717; border-color: #7a3b3b; }}
  }}
</style>
</head>
<body>
<h1>Set up the PU AI Sandbox</h1>
<p class="lede">{lede}</p>
{error}
{body}
{script}
</body>
</html>
"""

# Added to the page only when this computer has a file chooser to open, and
# only on the question that asks for a folder. The button asks the server —
# which is this same computer — to open its own Finder or Explorer window,
# because a browser's file box hands back a file's contents and never its
# location. See ``file_picker.py``.
_BROWSE_SCRIPT = """<script>
document.getElementById("browse-btn").addEventListener("click", async function () {
  var field = document.getElementById("folder");
  var button = this;
  button.disabled = true;
  var wording = button.textContent;
  button.textContent = "Choosing\\u2026";
  try {
    var res = await fetch("/pick", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ start: field.value })
    });
    var data = await res.json();
    // A window closed without choosing leaves what was already typed alone.
    if (data.path) { field.value = data.path; }
    else if (data.error) { alert(data.error); }
  } catch (e) {
    alert("The file chooser could not be opened. Type the folder instead.");
  } finally {
    button.disabled = false;
    button.textContent = wording;
  }
});
</script>"""


def _describe(candidate: first_run.ExtrasCandidate) -> str:
    """Return an HTML list of what was found at a location."""
    items = []
    if candidate.settings_file:
        n = candidate.people
        who = ("no one configured yet" if n == 0
               else "1 person configured" if n == 1 else f"{n} people configured")
        items.append(f"your settings and API keys <em>({who})</em>")
    if candidate.has_catalog:
        items.append("your model catalogue")
    if candidate.months:
        months = "1 month" if candidate.months == 1 else f"{candidate.months} months"
        items.append(f"your usage history <em>({months})</em>")
    return "<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>"


def _sync_note(path: Path) -> str:
    """Return the "this folder is being copied to the cloud" note, or nothing."""
    warning = paths.cloud_sync_warning(path)
    if not warning:
        return ""
    # quote=False because this lands in element content, not an attribute —
    # escaping apostrophes there only makes the sentence harder to read for
    # no gain in safety.
    return f'<div class="note">{html.escape(warning, quote=False)}</div>'


def _folder_field(value: str) -> str:
    """Return the box someone types a folder into, with its "Browse…" button.

    The button is left off entirely where there is no chooser to open, which
    leaves the box exactly as it was — typeable — rather than adding
    something that would do nothing when pressed.
    """
    browse = (
        '<button type="button" class="browse" id="browse-btn">Browse…</button>'
        if file_picker.available() else ""
    )
    return (
        '<label for="folder">Folder</label>'
        '<div class="field-row">'
        f'<input type="text" id="folder" name="folder" '
        f'value="{html.escape(value)}" spellcheck="false">'
        f"{browse}"
        "</div>"
    )


def _render(error: str = "") -> str:
    """Build the setup page, offering an existing folder if there is one."""
    found = first_run.find_existing()
    if found:
        candidate = found[0]
        safe_path = html.escape(str(candidate.path))
        # Two forms rather than one, because there are genuinely two answers
        # here and they submit different folders. The recommended one is a
        # single button with nothing to fill in; the other is folded away
        # behind a summary, since wanting it is the rare case.
        body = (
            '<form method="post" action="/">'
            '<fieldset class="found"><legend>Your files are already here</legend>'
            f"<p><code>{safe_path}</code></p>"
            f"{_describe(candidate)}"
            f'<input type="hidden" name="folder" value="{safe_path}">'
            f'<input type="hidden" name="acknowledged" value="{safe_path}">'
            "</fieldset>"
            f"{_sync_note(candidate.path)}"
            "<button type=\"submit\">Use these files</button>"
            "</form>"
            "<details><summary>My files are somewhere else</summary>"
            '<form method="post" action="/">'
            "<fieldset>"
            "<p>If you keep your files somewhere other than the folder above — "
            "on an external drive, say, or in a folder you chose yourself last "
            "time — point the sandbox at it here. Whatever is already in it is "
            "used as it is: nothing is overwritten, and no settings or usage "
            "history are lost.</p>"
            f"{_folder_field('')}"
            "</fieldset>"
            '<button type="submit" class="secondary">Use this folder instead</button>'
            "</form></details>"
        )
        lede = "One button, then you're done."
    else:
        default = paths.DEFAULT_EXTRAS_ROOT
        body = (
            '<form method="post" action="/">'
            "<fieldset><legend>Where should your files be kept?</legend>"
            "<p>Your API keys, your usage history and your saved conversations "
            "will live here. Keeping them outside the sandbox folder is what "
            "lets you replace the sandbox with a newer version later without "
            "losing any of it.</p>"
            f"{_folder_field(str(default))}"
            "<p>The suggestion above is a good one — press the button below to "
            "take it. Choose somewhere else only if you have a reason to.</p>"
            "</fieldset>"
            f"{_sync_note(default)}"
            f'<input type="hidden" name="acknowledged" value="{html.escape(str(default))}">'
            '<button type="submit">Create these files</button>'
            "</form>"
        )
        lede = "One question, then you're done."

    return _PAGE.format(
        body=body,
        lede=lede,
        script=_BROWSE_SCRIPT if file_picker.available() else "",
        error=(f'<div class="error">{html.escape(error, quote=False)}</div>'
               if error else ""),
    )


def _render_sync_confirm(chosen: Path, warning: str) -> str:
    """Ask once more about a folder that is being copied to a cloud service.

    Reached only for a folder somebody typed or browsed to themselves — the
    folder the page suggested carries its warning next to the button, where
    it was read before pressing it. This is for the one that didn't: a
    Dropbox or iCloud folder chosen because that is where their documents
    live, without the API keys and months of history that are about to be
    put there having been part of the thought.

    Nothing is refused. It is their computer and their files, and there are
    reasons to want this. It is only made deliberate.
    """
    safe_path = html.escape(str(chosen))
    body = (
        f'<div class="note">{html.escape(warning, quote=False)}</div>'
        '<form method="post" action="/">'
        # Nothing restated here that the warning above already says — one
        # copy of that reasoning, in paths.cloud_sync_warning().
        f"<fieldset><p>You chose <code>{safe_path}</code>.</p></fieldset>"
        f'<input type="hidden" name="folder" value="{safe_path}">'
        f'<input type="hidden" name="acknowledged" value="{safe_path}">'
        '<button type="submit">Use it anyway</button>'
        "</form>"
        '<p><a href="/">Choose somewhere else</a></p>'
    )
    return _PAGE.format(body=body, lede="One thing to check first.", script="", error="")


_DONE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Setup complete</title>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 16px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
         max-width: 40rem; margin: 4rem auto; padding: 0 1.25rem; text-align: center; }}
  code {{ background: rgba(128,128,128,.15); padding: .15rem .4rem; border-radius: 4px; }}
  @media (prefers-color-scheme: dark) {{ body {{ background: #14171a; color: #e6e6e6; }} }}
</style></head>
<body>
<h1>All set</h1>
<p>Your files are in <code>{where}</code>.</p>
<p>The sandbox is starting now — this page will move to it in a moment.</p>
<script>
// This page outlives the server that sent it: answering the question stops
// setup, and the sandbox proper starts on the same address a second or two
// later. How long that takes depends on the computer, so the page asks
// until someone answers rather than guessing a number and landing on an
// error page when the guess is short. The first wait is for the setup
// server to finish going away — while it is still up it would answer, and
// the answer would be this same page again.
setTimeout(function () {{
  (function ask() {{
    fetch("/", {{ method: "HEAD", cache: "no-store" }})
      .then(function () {{ location.href = "/"; }})
      .catch(function () {{ setTimeout(ask, 500); }});
  }})();
}}, 2000);
</script>
</body></html>
"""


def create_setup_app(on_complete) -> FastAPI:
    """Build the short-lived app that asks where a person's files should go.

    Args:
        on_complete: Called with the chosen folder once setup has finished,
                     so whatever started this server can stop it and get on
                     with launching the real interface.

    Returns:
        A FastAPI application serving the setup page at ``/``.
    """
    app = FastAPI()

    @app.get("/", response_class=HTMLResponse)
    async def show_form() -> str:
        return _render()

    # Not `async`: the chooser waits for a person to finish looking through
    # their files, and FastAPI gives an ordinary function its own thread to
    # wait on, leaving the page itself responsive.
    @app.post("/pick")
    def pick_folder(request: Request, body: PickFolderBody) -> JSONResponse:
        """Open this computer's folder chooser and report back what was picked."""
        # This server only ever listens on 127.0.0.1, so this can't normally
        # be reached from elsewhere. Checked anyway, because what's behind it
        # opens a window on the screen of whoever is running the sandbox.
        client = request.client.host if request.client else None
        if client not in ("127.0.0.1", "::1", "localhost"):
            return JSONResponse(
                {"path": None, "error": "The Browse button only works on this computer."},
                status_code=403,
            )
        try:
            chosen = file_picker.choose(
                kind="folder",
                start=body.start,
                prompt="Choose where the sandbox should keep your files",
            )
        except file_picker.PickerUnavailable as e:
            return JSONResponse({"path": None, "error": str(e)}, status_code=503)
        return JSONResponse({"path": str(chosen) if chosen else None})

    @app.post("/", response_class=HTMLResponse)
    async def submit(
        request: Request,
        folder: str = Form(...),
        acknowledged: str = Form(""),
    ) -> str:
        typed = folder.strip()
        if not typed:
            return _render(
                "No folder was given. Type where your files should be kept, or "
                "use the Browse button to find it."
            )
        chosen = Path(typed).expanduser()
        if not chosen.is_absolute():
            return _render(
                f"'{folder}' isn't a full path. It needs to start with a / so "
                "the sandbox knows exactly which folder you mean."
            )
        if chosen.exists() and not chosen.is_dir():
            return _render(f"{chosen} is a file, not a folder. Please choose another.")

        # The page shows this warning beside the folder it suggests, and says
        # so in the hidden field when it does. A folder somebody typed or
        # browsed to instead has had no such warning attached to it, so it
        # gets one now — the API keys are the point, and "my documents are in
        # Dropbox" is a perfectly ordinary reason to have picked it.
        sync_warning = paths.cloud_sync_warning(chosen)
        if sync_warning and acknowledged.strip() != str(chosen):
            return _render_sync_confirm(chosen, sync_warning)

        try:
            # Never initialises over a settings file — see first_run's module
            # docstring. Finding one means this is an existing setup, which
            # is carried forward instead.
            if not (chosen / paths.SETTINGS_FILENAME).exists():
                first_run.initialize_extras(chosen)
            first_run.complete_setup(chosen)
        except OSError as e:
            return _render(f"Could not prepare {chosen}: {e}")

        # Let the response finish rendering before the server is torn down.
        threading.Timer(0.5, on_complete, args=(chosen,)).start()
        return _DONE.format(where=html.escape(str(chosen)))

    return app
