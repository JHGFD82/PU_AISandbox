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

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from src import first_run, paths

file_picker = sys.modules["_pu_webui_file_picker"]


class PickFolderBody(BaseModel):
    """What the "Browse…" button sends: where the chooser should open."""

    start: str | None = None


class NewPersonBody(BaseModel):
    """One person to add during setup, and the key their work is billed to."""

    netid: str
    name: str
    key: str
    backup_key: str = ""


class NewModelBody(BaseModel):
    """A model to add, and whose key the few test requests are billed to."""

    provider_model: str
    professor: str

_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _page(body: str, lede: str, script: str = "", error: str = "") -> str:
    """Draw one setup page in the shell every other page of the sandbox uses.

    The shell was a string in this file with a stylesheet of its own — 28
    colours, none of them the sandbox's. Somebody installing this saw one piece
    of software and then, a moment later, what looked like another.

    It is a template beside the rest now, so it includes the same design
    system. Its layout stays its own: one narrow column, no top bar, nothing to
    navigate. An installer is not a page of the application.

    Args:
        body: The question being asked, as HTML.
        lede: The line under the heading.
        script: Any behaviour this page needs, as a <script> element.
        error: An explanation of what went wrong last time, as HTML.

    Returns:
        The whole page.
    """
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    environment = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html"]),
    )
    return environment.get_template("setup.html").render(
        body=body, lede=lede, script=script, error=error,
    )


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
        items.append("your model catalog")
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
            "<button type=\"submit\">Use these files, then continue to step 2</button>"
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
        # Files already here means the two questions after this one are very
        # likely answered too — the people and the models are in them.
        lede = "Your files are already here."
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
            '<button type="submit">Create these files, then continue to step 2</button>'
            "</form>"
        )
        lede = "Where should your files be kept?"

    return _page(
        body=_progress(1) + body,
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
    return _page(body=body, lede="One thing to check first.")


def _progress(step: int) -> str:
    """A quiet indication of how far through setup this is.

    Three questions, and "how much more of this is there" should not have to be
    guessed at. Understated on purpose: it is a reassurance, not the thing on
    the page worth looking at.

    Args:
        step: Which question this page is, counting from one.

    Returns:
        The bar and its label, as HTML.
    """
    filled = int(round(step / 3 * 100))
    return (
        '<div class="progress">'
        f'<div class="progress-bar"><span style="width: {filled}%"></span></div>'
        f'<p class="progress-label">Step {step} of 3</p>'
        "</div>"
    )


def _people_so_far() -> list:
    """Return the people already configured, worded as the page shows them."""
    from src.config import load_professor_config

    try:
        return [f"{entry['name']} ({netid})"
                for netid, entry in sorted(load_professor_config().items())]
    except Exception:
        return []


def _models_so_far() -> list:
    """Return the models already in the catalog, or none if it cannot be read."""
    from src.models import get_available_models

    try:
        return sorted(get_available_models())
    except Exception:
        return []


def _as_list(items: list, nothing_yet: str) -> str:
    """Render what has been added so far, or say that nothing has."""
    if not items:
        return f'<ul class="added"><li class="waiting">{nothing_yet}</li></ul>'
    rows = "".join(f"<li>{html.escape(item)}</li>" for item in items)
    return f'<ul class="added">{rows}</ul>'


def _panel_state(count: int):
    """Return the panel's class and its wording, given how many it holds."""
    if count == 0:
        return "needed", "Required"
    return "satisfied", ("1 added" if count == 1 else f"{count} added")


def _render_people(where) -> str:
    """Step two: who is using this, and the key their work is billed to.

    Its own page rather than half of one. Two questions on a page meant the
    second had to be kept in step with the first in the browser, and that is
    exactly where the picker came to be emptied of the name just added. A page
    that asks one thing is drawn from what is on disk each time it is asked
    for, and so has nothing to keep in step.
    """
    people = _people_so_far()
    state, wording = _panel_state(len(people))
    safe = html.escape(str(where))
    carry_on = "" if people else "disabled"
    # Nothing to say once the button works: pressing it is the answer, and a
    # line explaining that you may press it is one more thing to read past.
    hint = "" if people else "Add at least one person first."
    body = f"""
{_progress(2)}
<p>Your files are in <code>{safe}</code>. Now, who will be using this?</p>

<fieldset id="people" class="{state}">
  <legend>People <span class="rule"></span><span class="required-flag">{wording}</span></legend>
  <p>Each person needs their own Princeton AI Sandbox API key, which they get
     from OIT. Keys are kept in <code>{safe}</code> and are never shown again
     once saved. Add as many as you like; at least one is needed.</p>
  {_as_list(people, "Nobody added yet.")}
  <div class="after-a-list">
    <h3>Add a person</h3>
    <label for="netid">NetID <span class="waiting">&mdash; the university username, e.g. jh43</span></label>
    <input type="text" id="netid" autocapitalize="none" autocorrect="off" spellcheck="false">
    <label for="fullname">Display name <span class="waiting">&mdash; e.g. Jeff Heller</span></label>
    <input type="text" id="fullname">
    <label for="apikey">API key</label>
    <input type="password" id="apikey" autocomplete="off">
    <label for="backupkey">Backup key <span class="waiting">&mdash; optional; used if the first stops working</span></label>
    <input type="password" id="backupkey" autocomplete="off">
    <p class="row-error" id="people-error" hidden></p>
    <p><button type="button" id="add-person" class="secondary">Add this person</button>
       <span class="waiting" id="people-busy" hidden>Saving&hellip;</span></p>
  </div>
</fieldset>

<p><button type="button" id="continue" {carry_on}>Continue to step 3</button>
   <span class="waiting" id="continue-hint">{hint}</span></p>
"""
    return _page(body=body, lede="Who will be using this sandbox?",
                 script=_PEOPLE_SCRIPT)


def _render_models(where, billed_to: str = "") -> str:
    """Step three: what those people may send their work to.

    Args:
        where: The folder holding this installation's files.
        billed_to: Whose key was chosen last time, so the page comes back with
                   the same one still picked. Adding a model reloads this page,
                   and without this the browser would select whichever name
                   sorts first — quietly changing whose key the next test is
                   billed to, which is not a thing to change on somebody's
                   behalf.
    """
    from src.config import load_professor_config

    models = _models_so_far()
    people = _people_so_far()
    state, wording = _panel_state(len(models))
    # Built from what is on disk, so there is no list held in the browser to
    # keep in step with anything.
    options = "".join(
        f'<option value="{html.escape(netid)}"'
        f'{" selected" if netid == billed_to else ""}>'
        f'{html.escape(entry["name"])} ({html.escape(netid)})</option>'
        for netid, entry in sorted(load_professor_config().items())
    )
    finish = "" if models else "disabled"
    hint = "" if models else "Add at least one model first."
    who = "person" if len(people) == 1 else "people"
    body = f"""
{_progress(3)}
<p>{len(people)} {who} added. Last question: what may they send work to?</p>

<fieldset id="models" class="{state}">
  <legend>Models <span class="rule"></span><span class="required-flag">{wording}</span></legend>
  <p>Which models you can use depends on Princeton's AI Sandbox rather than on
     this software, so none are included here. <strong>Check Princeton's own AI
     Sandbox documentation</strong> for the models it currently offers, then add
     one below &mdash; named as its provider and then the model, like
     <code>openai/gpt-4o</code>.</p>
  {_as_list(models, "Nothing added yet.")}
  <div class="after-a-list">
    <h3>Add a model</h3>
    <p>Adding one looks up its price and then asks it a few one-token questions to
       find out what it can do. That takes a few seconds and a fraction of a cent,
       billed to the key you pick, and happens once.</p>
    <label for="modelname">Model</label>
    <input type="text" id="modelname" placeholder="openai/gpt-4o"
           autocapitalize="none" autocorrect="off" spellcheck="false">
    <label for="modelprof">Test with whose key</label>
    <select id="modelprof">{options}</select>
    <p class="row-error" id="models-error" hidden></p>
    <p><button type="button" id="add-model" class="secondary">Add and test</button>
       <span class="waiting" id="models-busy" hidden>Asking the model what it can do&hellip;</span></p>
  </div>
</fieldset>

<p><button type="button" id="finish" {finish}>Finish setup</button>
   <span class="waiting" id="finish-hint">{hint}</span></p>
<p><a href="/people">Back to step 2</a></p>
"""
    return _page(body=body, lede="What may they send work to?",
                 script=_MODELS_SCRIPT)


# Shared by both pages: sending one thing, and saying so when it did not work.
_SEND = """
function show(id, on) { const n = document.getElementById(id); if (n) n.hidden = !on; }

function fail(where, message) {
  const box = document.getElementById(where + "-error");
  box.textContent = message;
  box.hidden = false;
}

async function send(path, payload) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.detail || body.error || res.statusText);
  return body;
}
"""

_PEOPLE_SCRIPT = "<script>" + _SEND + """
document.getElementById("add-person").addEventListener("click", async () => {
  document.getElementById("people-error").hidden = true;
  const netid = document.getElementById("netid").value.trim();
  const name = document.getElementById("fullname").value.trim();
  const key = document.getElementById("apikey").value;
  const backup = document.getElementById("backupkey").value;
  if (!netid || !name || !key) {
    fail("people", "A netID, a display name and an API key are all needed.");
    return;
  }
  show("people-busy", true);
  try {
    await send("/people", { netid, name, key, backup_key: backup });
    // Asking for the page again rather than patching it here. The page is
    // built from what is on disk, so a reload shows exactly what was saved —
    // nothing to keep in step, and nothing to get wrong keeping it.
    location.reload();
  } catch (e) {
    fail("people", e.message);
    show("people-busy", false);
  }
});

document.getElementById("continue").addEventListener("click", () => {
  location.href = "/models";
});
</script>"""

_MODELS_SCRIPT = "<script>" + _SEND + """
document.getElementById("add-model").addEventListener("click", async () => {
  document.getElementById("models-error").hidden = true;
  const model = document.getElementById("modelname").value.trim();
  const professor = document.getElementById("modelprof").value;
  if (!model) { fail("models", "Type a model name, like openai/gpt-4o."); return; }
  if (!professor) { fail("models", "Choose whose key the test should be billed to."); return; }
  show("models-busy", true);
  document.getElementById("add-model").disabled = true;
  try {
    await send("/models", { provider_model: model, professor });
    // Reloaded with the same person still chosen. A plain reload would leave
    // the browser to pick whichever name sorts first, quietly moving whose key
    // the next test is billed to.
    location.href = "/models?billed_to=" + encodeURIComponent(professor);
  } catch (e) {
    fail("models", e.message);
    show("models-busy", false);
    document.getElementById("add-model").disabled = false;
  }
});

document.getElementById("finish").addEventListener("click", async () => {
  const button = document.getElementById("finish");
  button.disabled = true;
  document.getElementById("finish-hint").textContent = "Starting the sandbox\u2026";
  try {
    await send("/finish", {});
    const heading = document.createElement("h1");
    heading.textContent = "All set";
    const said = document.createElement("p");
    said.textContent =
      "The sandbox is starting now \u2014 this page will move to it in a moment.";
    document.body.replaceChildren(heading, said);
    // This page outlives the server that sent it: finishing stops setup, and
    // the sandbox proper starts on the same address a second or two later. How
    // long that takes depends on the computer, so the page asks until someone
    // answers rather than guessing a number and landing on an error page when
    // the guess is short.
    setTimeout(function () {
      (function ask() {
        fetch("/", { method: "HEAD", cache: "no-store" })
          .then(function () { location.href = "/"; })
          .catch(function () { setTimeout(ask, 500); });
      })();
    }, 1500);
  } catch (e) {
    button.disabled = false;
    document.getElementById("finish-hint").textContent = e.message;
  }
});
</script>"""


# There is no separate "all set" page any more. Setup no longer ends when the
# folder exists — it carries on to ask who is using this and what they may send
# to — so the last page is the one holding those two questions, and it says "All
# set" in place once they are answered. Its waiting-for-the-server-to-go-away
# loop is the one that used to live here, kept because guessing a number instead
# lands on an error page whenever the guess is short.


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

        # Not finished yet. The folder is only the first of three things this
        # has to end with — the other two are somebody to bill and something to
        # send to, and neither can be guessed. Setup carries on to ask for them
        # rather than handing over a sandbox that cannot do anything.
        return _render_people(chosen)

    @app.get("/people", response_class=HTMLResponse)
    async def people_page() -> str:
        """Step two, drawn from what is on disk right now."""
        return _render_people(paths.extras_root())

    @app.get("/models", response_class=HTMLResponse)
    async def models_page(billed_to: str = ""):
        """Step three. Sent back a step if there is nobody to bill a test to.

        Args:
            billed_to: Carried in the address when this page reloads itself, so
                       whoever was chosen stays chosen.
        """
        from src.config import load_professor_config

        if not load_professor_config():
            return RedirectResponse("/people", status_code=303)
        # billed_to comes from the address bar and is passed on unchecked,
        # which is safe for the one reason that matters: it is only ever
        # compared against the netIDs already configured, and never written
        # into the page. A name that is not one of them simply matches nothing
        # and no option is marked as chosen.
        return HTMLResponse(_render_models(paths.extras_root(), billed_to))

    @app.post("/people")
    async def add_person(body: NewPersonBody) -> JSONResponse:
        """Record one person and their API key.

        Imported here rather than at the top of the file: the settings store
        works out where it writes from the marker that ``complete_setup()`` has
        only just created, and this module is imported before that has happened.
        """
        from src import settings_store
        from src.errors import CLIError

        try:
            netid = settings_store.add_professor(
                body.netid.strip(), body.name.strip(), body.key,
                body.backup_key.strip() or None,
            )
        except (CLIError, ValueError) as e:
            # Both, because a netID that isn't one and a name that is blank
            # come back as different types, and either is something the person
            # can put right — not a fault to hand them a 500 for.
            raise HTTPException(400, str(e)) from e
        return JSONResponse({
            "netid": netid,
            "label": f"{body.name.strip()} ({netid})",
        })

    @app.post("/models")
    def add_model(body: NewModelBody) -> JSONResponse:
        """Look a model up, find out what it can do, and record it.

        Not ``async``: this makes several small requests to the provider and
        waits on each. On the event loop that would stop the page answering
        anything else — including the person giving up and pressing Finish.
        """
        from src.config import get_api_key
        from src.errors import CLIError
        from src.models import add_model_to_catalog

        name = body.provider_model.strip()
        if "/" not in name:
            raise HTTPException(
                400,
                "A model is named as its provider and then the model, separated "
                "by a slash — for example openai/gpt-4o.",
            )
        try:
            api_key, _ = get_api_key(body.professor.strip())
            model_name, _entry = add_model_to_catalog(name, api_key=api_key)
        except (CLIError, ValueError) as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:
            # Almost always the price lookup: a misspelled name, or a provider
            # the pricing service doesn't know. Either way it wasn't added.
            raise HTTPException(400, str(e)) from e
        return JSONResponse({"model": model_name, "label": model_name})

    @app.post("/finish")
    async def finish() -> JSONResponse:
        """End setup, once there is somebody to bill and something to send to.

        Checked here and not only in the browser. The page disables the button
        until both exist, but the button is not what makes it true.
        """
        from src.config import load_professor_config
        from src.models import get_available_models

        if not load_professor_config():
            raise HTTPException(400, "Add at least one person before finishing.")
        try:
            models = get_available_models()
        except Exception:
            # A catalog that cannot be read counts as no models. During setup
            # that is a thing to say plainly and let somebody act on, not a
            # fault to hand them a 500 for.
            models = []
        if not models:
            raise HTTPException(400, "Add at least one model before finishing.")

        # Let the response reach the browser before the server is torn down.
        threading.Timer(0.5, on_complete, args=(paths.extras_root(),)).start()
        return JSONResponse({"ok": True})

    return app
