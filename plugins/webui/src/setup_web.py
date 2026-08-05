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
from fastapi.responses import HTMLResponse, JSONResponse
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
  /* A panel holding something that has to be filled in before setup can end.
     The colour is a reminder, not the message: "Required" is written inside it
     as well, because a border somebody cannot distinguish is no signal at all.
     It fades rather than snaps, so the change is noticed by someone who has
     just pressed Add and is looking at the list, not at the edge. */
  fieldset.needed {{ border: 2px solid #c0392b; }}
  fieldset.satisfied {{ border: 2px solid #ccc; transition: border-color .5s ease; }}
  .required-flag {{ color: #c0392b; font-weight: 600; font-size: .85rem; }}
  fieldset.satisfied .required-flag {{ color: #3f7a45; }}
  .added {{ margin: .5rem 0 0; padding-left: 1.2rem; }}
  .added li {{ margin: .15rem 0; }}
  .waiting {{ color: #666; font-style: italic; }}
  .row-error {{ color: #c0392b; margin: .6rem 0 0; }}
  .field-row {{ display: flex; gap: .5rem; align-items: center; }}
  .field-row input[type=text] {{ flex: 1; }}
  input[type=password] {{ width: 100%; padding: .55rem; font: inherit; border: 1px solid #999; border-radius: 6px; }}
  select {{ width: 100%; padding: .55rem; font: inherit; border: 1px solid #999; border-radius: 6px; }}
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
    fieldset.needed {{ border-color: #e07a6a; }}
    fieldset.satisfied {{ border-color: #333; }}
    .required-flag, .row-error {{ color: #e07a6a; }}
    fieldset.satisfied .required-flag {{ color: #8fc493; }}
    .waiting {{ color: #9aa4ad; }}
    input[type=password], select {{ background: #1d2126; color: #e6e6e6; border-color: #444; }}
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
        # Files already here means the two questions after this one are very
        # likely answered too — the people and the models are in them.
        lede = "One button, and then a check that nothing is missing."
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
        lede = "Three questions: where your files go, who is using this, and what they may send to."

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


def _render_people_and_models(where: Path) -> str:
    """The second question: who is using this, and what may they send to.

    Setup used to end once the folder existed. That left a person with three
    files and nothing that worked: no API key to bill, and no model to send
    anything to. Both have to be asked for, and neither can be guessed — a key
    is a private credential, and which models exist depends on the institution's
    own AI sandbox.

    Both panels are marked as required until they hold something. The border is
    a reminder rather than the message: colour alone excludes anyone who cannot
    tell these two apart, so each panel also says so in words, and says how many
    it has once it has any.

    Args:
        where: The folder just created, named so the page can say where the
               keys about to be typed will be kept.

    Returns:
        The page, ready to serve.
    """
    safe = html.escape(str(where))
    body = f"""
<p>Your files are in <code>{safe}</code>. Two more things and the sandbox is
ready to use. Both are needed, and neither can be filled in for you.</p>

<fieldset id="people" class="needed">
  <legend>Who will be using this <span class="required-flag" id="people-flag">Required</span></legend>
  <p>Each person needs their own Princeton AI Sandbox API key, which they get
     from OIT. Keys are kept in <code>{safe}</code> and are never shown again
     once saved.</p>
  <ul class="added" id="people-list"><li class="waiting">Nobody added yet.</li></ul>
  <label for="netid">NetID <span class="waiting">— the university username, e.g. jh43</span></label>
  <input type="text" id="netid" autocapitalize="none" autocorrect="off" spellcheck="false">
  <label for="fullname">Display name <span class="waiting">— e.g. Jeff Heller</span></label>
  <input type="text" id="fullname">
  <label for="apikey">API key</label>
  <input type="password" id="apikey" autocomplete="off">
  <label for="backupkey">Backup key <span class="waiting">— optional; used if the first stops working</span></label>
  <input type="password" id="backupkey" autocomplete="off">
  <p class="row-error" id="people-error" hidden></p>
  <p><button type="button" id="add-person">Add this person</button>
     <span class="waiting" id="people-busy" hidden>Saving…</span></p>
</fieldset>

<fieldset id="models" class="needed">
  <legend>Models <span class="required-flag" id="models-flag">Required</span></legend>
  <p>Which models you can use depends on Princeton's AI Sandbox rather than on
     this software, so none are included here. <strong>Check Princeton's own AI
     Sandbox documentation</strong> for the models it currently offers, then add
     one below — named as its provider and then the model, like
     <code>openai/gpt-4o</code>.</p>
  <p>Adding one looks up its price and then asks it a few one-token questions to
     find out what it can do. That takes a few seconds and a fraction of a cent,
     billed to the key you pick, and happens once.</p>
  <ul class="added" id="models-list"><li class="waiting">Nothing added yet.</li></ul>
  <label for="modelname">Model</label>
  <input type="text" id="modelname" placeholder="openai/gpt-4o"
         autocapitalize="none" autocorrect="off" spellcheck="false">
  <label for="modelprof">Test with whose key</label>
  <select id="modelprof"><option value="">Add someone above first</option></select>
  <p class="row-error" id="models-error" hidden></p>
  <p><button type="button" id="add-model" disabled>Add and test</button>
     <span class="waiting" id="models-busy" hidden>Asking the model what it can do…</span></p>
</fieldset>

<p><button type="button" id="finish" disabled>Finish setup</button>
   <span class="waiting" id="finish-hint">Add at least one person and one model first.</span></p>
"""
    return _PAGE.format(
        body=body,
        lede="Two more things, then you're done.",
        error="",
        script=_STEP_TWO_SCRIPT,
    )


_STEP_TWO_SCRIPT = """
<script>
// The page keeps its own count of what has been added, because that is what
// decides whether setup may end. The server is the authority on whether an
// addition worked; this only reflects what it said.
const state = { people: [], models: [] };

function show(id, on) { document.getElementById(id).hidden = !on; }

function fail(where, message) {
  const box = document.getElementById(where + "-error");
  box.textContent = message;
  box.hidden = false;
}

function clearFailure(where) { document.getElementById(where + "-error").hidden = true; }

function paint() {
  for (const which of ["people", "models"]) {
    const held = state[which];
    const panel = document.getElementById(which);
    // Red until it holds something, then grey. Both classes are named, rather
    // than one being the absence of the other, so the fade has something to
    // fade between.
    panel.classList.toggle("needed", held.length === 0);
    panel.classList.toggle("satisfied", held.length > 0);
    const flag = document.getElementById(which + "-flag");
    flag.textContent = held.length === 0
      ? "Required"
      : (held.length === 1 ? "1 added" : held.length + " added");
    const list = document.getElementById(which + "-list");
    list.replaceChildren();
    if (!held.length) {
      const empty = document.createElement("li");
      empty.className = "waiting";
      empty.textContent = which === "people" ? "Nobody added yet." : "Nothing added yet.";
      list.appendChild(empty);
    } else {
      held.forEach(text => {
        const row = document.createElement("li");
        row.textContent = text;
        list.appendChild(row);
      });
    }
  }
  // A model is tested with somebody's key, so there has to be somebody first.
  const picker = document.getElementById("modelprof");
  document.getElementById("add-model").disabled = state.people.length === 0;
  const ready = state.people.length > 0 && state.models.length > 0;
  document.getElementById("finish").disabled = !ready;
  document.getElementById("finish-hint").textContent = ready
    ? "You can add more of either, or finish now."
    : (state.people.length === 0
        ? "Add at least one person and one model first."
        : "Now add at least one model.");
  if (state.people.length && picker.options.length
      && picker.options[0].value === "") {
    picker.replaceChildren();
  }
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

document.getElementById("add-person").addEventListener("click", async () => {
  clearFailure("people");
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
    const added = await send("/people", { netid, name, key, backup_key: backup });
    state.people.push(added.label);
    const option = document.createElement("option");
    option.value = added.netid;
    option.textContent = added.label;
    document.getElementById("modelprof").appendChild(option);
    ["netid", "fullname", "apikey", "backupkey"].forEach(
      id => { document.getElementById(id).value = ""; });
    paint();
  } catch (e) {
    fail("people", e.message);
  } finally {
    show("people-busy", false);
  }
});

document.getElementById("add-model").addEventListener("click", async () => {
  clearFailure("models");
  const model = document.getElementById("modelname").value.trim();
  const professor = document.getElementById("modelprof").value;
  if (!model) { fail("models", "Type a model name, like openai/gpt-4o."); return; }
  if (!professor) { fail("models", "Choose whose key the test should be billed to."); return; }
  show("models-busy", true);
  document.getElementById("add-model").disabled = true;
  try {
    const added = await send("/models", { provider_model: model, professor });
    state.models.push(added.label);
    document.getElementById("modelname").value = "";
    paint();
  } catch (e) {
    fail("models", e.message);
  } finally {
    show("models-busy", false);
    document.getElementById("add-model").disabled = state.people.length === 0;
  }
});

document.getElementById("finish").addEventListener("click", async () => {
  document.getElementById("finish").disabled = true;
  document.getElementById("finish-hint").textContent = "Starting the sandbox…";
  try {
    await send("/finish", {});
    const heading = document.createElement("h1");
    heading.textContent = "All set";
    const said = document.createElement("p");
    said.textContent =
      "The sandbox is starting now — this page will move to it in a moment.";
    document.body.replaceChildren(heading, said);
    // This page outlives the server that sent it: finishing stops setup, and
    // the sandbox proper starts on the same address a second or two later. How
    // long that takes depends on the computer, so the page asks until someone
    // answers rather than guessing a number and landing on an error when the
    // guess is short. The first wait is for the setup server to finish going
    // away — while it is still up it would answer, and the answer would be
    // this same page again.
    setTimeout(function () {
      (function ask() {
        fetch("/", { method: "HEAD", cache: "no-store" })
          .then(function () { location.href = "/"; })
          .catch(function () { setTimeout(ask, 500); });
      })();
    }, 1500);
  } catch (e) {
    document.getElementById("finish").disabled = false;
    document.getElementById("finish-hint").textContent = e.message;
  }
});

paint();
</script>
"""


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
        return _render_people_and_models(chosen)

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
            # A catalogue that cannot be read counts as no models. During setup
            # that is a thing to say plainly and let somebody act on, not a
            # fault to hand them a 500 for.
            models = []
        if not models:
            raise HTTPException(400, "Add at least one model before finishing.")

        # Let the response reach the browser before the server is torn down.
        threading.Timer(0.5, on_complete, args=(paths.extras_root(),)).start()
        return JSONResponse({"ok": True})

    return app
