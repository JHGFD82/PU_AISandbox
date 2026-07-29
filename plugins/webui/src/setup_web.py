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
import threading
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse

from src import first_run, paths

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
<p class="lede">One question, then you're done.</p>
{error}
<form method="post" action="/">
{body}
{warning}
  <button type="submit">{button}</button>
</form>
</body>
</html>
"""


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


def _render(error: str = "") -> str:
    """Build the setup page, offering an existing folder if there is one."""
    found = first_run.find_existing()
    if found:
        candidate = found[0]
        body = (
            '<fieldset class="found"><legend>Your files are already here</legend>'
            f"<p><code>{html.escape(str(candidate.path))}</code></p>"
            f"{_describe(candidate)}"
            f'<input type="hidden" name="folder" value="{html.escape(str(candidate.path))}">'
            "</fieldset>"
        )
        button = "Use these files"
        target = candidate.path
    else:
        default = paths.DEFAULT_EXTRAS_ROOT
        body = (
            "<fieldset><legend>Where should your files be kept?</legend>"
            "<p>Your API keys, your usage history and your saved conversations "
            "will live here. Keeping them outside the sandbox folder is what "
            "lets you replace the sandbox with a newer version later without "
            "losing any of it.</p>"
            '<label for="folder">Folder</label>'
            f'<input type="text" id="folder" name="folder" '
            f'value="{html.escape(str(default))}" spellcheck="false">'
            "</fieldset>"
        )
        button = "Create these files"
        target = default

    warning = ""
    sync = paths.cloud_sync_warning(target)
    if sync:
        warning = f'<div class="note">{html.escape(sync, quote=False)}</div>'

    return _PAGE.format(
        body=body,
        button=button,
        warning=warning,
        # quote=False because this lands in element content, not an
        # attribute — escaping apostrophes there only makes the sentence
        # harder to read for no gain in safety.
        error=(f'<div class="error">{html.escape(error, quote=False)}</div>'
               if error else ""),
    )


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

    @app.post("/", response_class=HTMLResponse)
    async def submit(request: Request, folder: str = Form(...)) -> str:
        chosen = Path(folder.strip()).expanduser()
        if not chosen.is_absolute():
            return _render(
                f"'{folder}' isn't a full path. It needs to start with a / so "
                "the sandbox knows exactly which folder you mean."
            )
        if chosen.exists() and not chosen.is_dir():
            return _render(f"{chosen} is a file, not a folder. Please choose another.")

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
