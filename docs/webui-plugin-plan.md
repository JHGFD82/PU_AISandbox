# Web UI Plugin — Design Plan

> **Superseded references (2026-07):** this document predates the settings
> consolidation that replaced `.env`, `apis.json`, and `data_sources.json`
> with a single `.settings` file plus `settings.default.toml` /
> `settings.shared.toml` / `settings.local.toml` layering. Where this plan
> mentions `.env`, `data_sources.json`, or `src/tracking/source_config.py`,
> the current equivalents are `.settings` and `src/settings_store.py` — see
> `docs/configuration.md` for the up-to-date picture. The narrative below is
> left as-is as a historical record of the design reasoning; only the file
> names it refers to have changed.

Status, on branch `feature/webui-and-remote-sources`:
- Section 1 (external/remote usage-data sources, the migration script, the
  `usage sources` CLI) — **built.** Checked against this installation's real
  `data/` on 2026-07-24: every existing record (all ~6,045 of them, back to
  July 2025) already had a `source` tag, because `TokenTracker` has been
  stamping it on every write since section 1 was merged — there were never
  any pre-section-1 records left to convert. The migration script itself
  was not run (nothing for it to do), and is still there for the
  hypothetical case of importing genuinely old, pre-feature data later.
- Sections 2-7 (the web UI plugin itself: `requires_professor` core
  plumbing, the `plugins/webui/` skeleton, the passphrase auth gate,
  professor switcher, non-streaming chat, model picker, and the spend
  sidebar's current-month view) — **built.** Manually/structurally
  verified in a sandbox without network access to install this project's
  real dependencies (same limitation as section 1 — see that section's
  verification note); **not yet run through the real `pytest` suite on a
  machine with `fastapi`/`uvicorn`/etc. installed.**
- The `/settings` page (2026-07-24, after the settings consolidation above)
  — **built.** A browser front-end over `src/settings_store.py`: add/remove
  professors and replace their keys, set the webui passphrase (hashed
  server-side, via a dedicated endpoint — never stored or transmitted as a
  plain value once set) and session secret, set the shared-settings pointer
  and any alternate-endpoint credentials (endpoint *definitions* stay
  hand-edited TOML — the page shows them read-only with a copyable
  snippet), and manage external usage-data sources. A first-time visitor
  with zero professors configured is redirected to `/settings` instead of
  an empty chat screen; the section order flips between "professors first"
  (nothing configured yet) and "shared settings first" (at least one
  professor already exists) since that's the more likely thing someone
  returns to tweak. Covered by `plugins/webui/tests/test_app.py`'s
  `TestSettingsPage`/`TestSettingsProfessors`/`TestSettingsPassphrase`/
  `TestSettingsValues`/`TestSettingsSources` classes, run through the real
  `pytest` suite.
- Streaming responses (2026-07-24) — **built.** `/api/chat` now returns a
  `text/event-stream` `StreamingResponse` instead of one JSON blob:
  `ChatService.stream_message()` (`plugins/webui/src/services/chat_service.py`)
  wraps `BaseService._create_completion_stream()` (`stream=True`,
  `stream_options={"include_usage": True}`) and yields one `delta` event per
  chunk of text plus a final `done` event; `chat.html` reads the response
  body with `fetch()` + a `ReadableStream` reader (not `EventSource`, which
  can't send a POST body) and appends each `delta` to the assistant bubble
  as it arrives. The user's own message is now saved to the conversation
  store immediately, before the model call starts — this is also what
  powers the sent message's brief "waiting..." indicator, which is removed
  as soon as the first `delta` arrives (or the whole turn is resynced from
  the server on failure). Usage/cost is read off the stream's final chunk;
  if a streamed response is ever missing usage data, that turn is logged and
  left unbilled rather than raising, the same tolerance `send_message()`
  already had for a non-streamed reply — this was an explicit tradeoff since
  Portkey's docs don't confirm usage is always present on a streamed Claude
  response, and blocking the feature on a live test wasn't worth it. Covered
  by `plugins/webui/tests/test_chat_service.py`'s `TestStreamMessage` class
  and `plugins/webui/tests/test_app.py`'s `TestChat` class (SSE event
  parsing, missing-usage fallback, and the user message surviving a
  mid-stream failure), run through the real `pytest` suite.
- Historical + combined-sources usage in the sidebar, compaction,
  `CasBackend`, and memory notes (the rest of section 9's build order) —
  **still planning only.**

Two real deviations were found and fixed while building sections 2-7,
worth recording here because both contradict something stated earlier in
this document as already confirmed:

1. **The "confirmed by testing argparse" claim in section 2 was wrong.**
   `python main.py webui serve` (two tokens after the optional `professor`
   positional) does *not* parse correctly on its own — argparse only
   resolves the professor-vs-command ambiguity correctly when exactly one
   token follows, not two or more (a known rough edge of mixing an optional
   positional with subparsers). What was actually tested before was
   apparently just `python main.py webui` alone. Fixed with a small
   pre-parsing step in `src/cli.py` (`_insert_professor_placeholder_if_needed`)
   that detects a `requires_professor = False` command as the first
   argument and inserts an empty-string placeholder so argparse routes
   everything correctly regardless of how many tokens follow. This is now
   part of the "changes required outside plugins/" list in section 5.
2. **Section 3's `from .src.app import run_server` sketch doesn't work
   as written.** Plugins in this project are loaded by executing
   `plugin.py` under a *fabricated* module name
   (`pu_plugin.<name>.plugin`) whose parent package never actually exists
   (see `src/runtime/plugin_loader.py`) — a literal relative import
   inside that file fails the instant Python's real import machinery tries
   to resolve the non-existent parent. The existing `_register()` pattern
   already works around exactly this for plugin-owned *services*
   (`src.services.<name>`) and *settings* (`pu_plugin.<name>.settings`),
   because their only consumers (`SandboxProcessor.__getattr__`,
   `src.settings.__getattr__`) look them up via a direct `sys.modules`
   dictionary access, never a real import statement. `plugin.py` calling
   into its *own* `app.py`, and `app.py` calling into its own `auth.py`/
   `conversation.py`, don't have that luxury — those really are literal
   import statements. Fixed by registering those three files under flat,
   dot-free names (`_pu_webui_app`, `_pu_webui_auth`,
   `_pu_webui_conversation`) instead of a deeper fabricated dotted path —
   a dot-free name resolves straight out of `sys.modules` with no parent
   chain to fail on. `src.services.chat_service` (this plugin's actual
   AI-calling code) keeps the normal dotted convention, since
   `src.services` is a real, already-existing package and that's
   specifically what lets `SandboxProcessor` find it. See the module
   docstring in `plugins/webui/src/app.py` for the full explanation kept
   next to the code itself.

This plans out a new `plugins/webui/` plugin that gives you a browser-based
chat interface (conversation history, memory, context compaction, model
switching, and a live spending sidebar) on top of the existing PortKey
Sandbox, plus a related but independent capability: pulling spend data in
from *other* installations of this package that other people are running,
so a single report can cover everyone you're tracking, not just the
professors configured in your own `.env`.

Revision history:
- The first draft assumed a multi-user server where each professor logs in
  separately. That's wrong for how this package is actually used — **one
  individual runs a single installation**, either as the professor
  themselves or on behalf of several. Corrected to: one unlock gate, no
  per-professor login identity, and a professor *switcher* inside the app
  instead (§2/§4).
- That correction also removed Princeton CAS from the plan, on the
  (mistaken) assumption it only mattered for separating multiple people's
  logins. It's back in, scoped differently: even with a single unlock
  gate, CAS is worth building as *that* gate's eventual backend, because
  it gives the professor the same login they already use everywhere else
  at Princeton, and keeps this tool inside whatever authentication
  standard OIT expects of university-facing services — not because it
  needs to distinguish between multiple people (§4).
- Also added: an interactive setup command for the external-source config
  in §1, so the two-machine settings don't have to be hand-edited and
  kept in sync by eye.

---

## 1. External / remote usage-data sources

You said you'd want this addressed first, and it's a smaller, more
self-contained change than the web UI itself — it touches only the
tracking layer and the existing `data/visualize_usage.py` script, with no
new plugin required. Worth building on its own regardless of whether the
web UI happens next.

### The problem, restated after your follow-up

There are actually two related problems, and they need different fixes:

1. **Visibility.** Every installation only reads/writes its own
   `<project folder>/data/`. If a professor runs their own copy, you can't
   see their spend unless you go looking for their file.
2. **Symmetric multi-writer activity.** You don't only *view* a
   professor's account — you sometimes *act on it* from your own
   installation (their API key, run under your machine). Today that
   activity lands in *your* local `data/token_usage_smith.json`, not
   theirs, so even if they later get read access to your files, it's on
   you to have wired that up — and if you two ever both write into the
   literal same file (e.g. by pointing `data_dir` at one shared Dropbox
   path), Dropbox syncing can silently corrupt it. Dropbox isn't a real
   shared filesystem — it's two independent local copies that reconcile
   *after the fact* — so two processes doing a read-modify-write on "the
   same" file can race, and OS-level file locking (`flock`, `filelock`)
   doesn't help, because a lock held on your machine has no effect on the
   professor's machine until Dropbox syncs. Its usual fallback is to save
   a `filename (conflicted copy from ...).json` next to the original,
   which is exactly the silent, easy-to-miss data loss you'd want to
   avoid for anything billing-related.

The fix for both is the same underlying idea: **stop having two machines
edit one mutable file, and instead have each machine only ever create new,
uniquely-named files that nobody edits again.** Dropbox syncing many small
files that are each written once is completely safe — there's nothing to
conflict, because no file is ever the target of two different writes. This
is the same reason apps like Obsidian keep each note as its own file
rather than one shared database.

### The design: two source modes, most professors need neither

**`read-only`** (the common case — one person, or one installation, is
the only writer): unchanged from the earlier draft — register another
installation's real `data/` folder as a source, reached over whatever
synced or shared path gets you there. No new file format, nothing to
build beyond the aggregation helpers below. Safe because there's still
only one writer; conflicts can't happen if only one side ever edits.

**`shared-write`** (your actual scenario — both you and the professor
record usage against the same professor identity): opt in per professor.
Both installations point at the same synced directory, and for *that
professor only*, `TokenTracker` stops rewriting one monthly JSON file and
instead appends one small, immutable, uniquely-named file per API call:

```
{shared_dir}/events/smith/2026-07/
    20260724T101533_4f2a1c_toms-mac.json
    20260724T103000_9b7e02_smiths-imac.json
    20260724T114501_0cd913_toms-mac.json
```

Each file is one call's record — the same fields `TokenUsage` already has
(model, token counts, costs, timestamp) — plus a `source` field (e.g.
`"toms-mac"`, set once per installation in `settings.local.toml`) marking
who made the call. The filename bundles a timestamp, a short random
suffix, and the source, so two installations can never generate the same
filename — there's no scenario where Dropbox sees two edits to one path,
which is what makes this safe over a plain file-sync service with no
locking or transactions.

This also gives you the visibility split you actually asked for, for
free: because both installations read and write the *same* directory,
reports on either end automatically include everyone's activity on that
professor, and the `source` tag lets a report break it out as "your
activity on this account" vs. "theirs" rather than just a combined total.

Monthly/daily/model totals are computed by summing whatever event files
exist for a month, the same way `get_all_time_usage()` already sums
across archive files today — just at finer grain. Once a month is over and
nobody's actively writing to it anymore, a rollover step folds that
month's event files into one summary file in the existing archive shape
(`archives/{professor}/{YYYY-MM}.json`), so the event-file directory only
ever holds the current month's still-growing set of small files, not an
unbounded pile. That fold-up is safe precisely because it only ever
touches a month that's already closed — nobody is still writing to it.

### Configuration — Status: **built**, on branch `feature/webui-and-remote-sources`

Built slightly differently than first sketched here, and worth recording
why: rather than `settings.local.toml`, this is stored in a dedicated
git-ignored `data_sources.json` at the repo root (with a committed
`data_sources.template.json` alongside it, mirroring how `apis.json` and
`src/model_catalog.json` already work). Reasons for the change, made while
implementing:

- The Python standard library can only *read* TOML (`tomllib`), not write
  it. Writing `settings.local.toml` programmatically from `usage sources
  add` would have meant either adding a new TOML-writing dependency, or
  rewriting the whole file from scratch on every change — which would
  silently discard any comments or formatting a person had added by hand.
- `settings.toml`/`settings.local.toml` hold behavioral tuning knobs
  (temperature, retry counts, thresholds); `data_sources.json` holds
  per-installation state that a command manages on your behalf, which is
  exactly the role `model_catalog.json`/`apis.json` already play in this
  project. Keeping it as its own JSON file, read/written with the same
  atomic-write pattern `save_model_catalog()` already uses, needed no new
  dependency and matches an existing convention instead of inventing a new
  one.

```json
// data_sources.json (per-installation, git-ignored)
{
  "source_id": "toms-mac",
  "external_sources": [
    {
      "label": "Prof. Johnson",
      "path": "/Volumes/shared-drive/johnson-sandbox/data",
      "mode": "read-only",
      "professor": null
    },
    {
      "label": "Prof. Smith",
      "path": "/Users/heller/Dropbox/Smith-Shared/data",
      "mode": "shared-write",
      "professor": "smith"
    }
  ]
}
```

Read/write logic lives in **`src/tracking/source_config.py`** (new file):
`get_source_id()` (hostname fallback), `get_configured_sources()`,
`get_shared_write_source(professor)`, `add_source()`, `remove_source()`.
`src/tracking/token_tracker.py` imports from it — no change to
`src/settings.py` was needed after all, since this data never goes through
the TOML settings system.

### Core changes — Status: **built**

- **`src/tracking/token_tracker.py`**
  - `TokenTracker.__init__` checks `get_shared_write_source(professor)`
    whenever it's constructed the normal way (no explicit `data_file`
    override, which is how every real caller — `BaseService`,
    `SandboxProcessor` — already builds one). If a shared-write source is
    configured for that professor, the tracker switches to `source_mode =
    "shared-write"` and every public method (`record_usage`,
    `get_daily_usage`, `get_monthly_usage`, `get_all_time_usage`,
    `list_archived_months`) transparently uses the event-file read/write
    path described above instead of the local mutable-file path — callers
    never need to check which mode they're in. Tests that pass an explicit
    `data_file` (i.e. the entire existing test suite) are completely
    unaffected, since that's the gate this new behavior sits behind.
  - `_rollover_closed_shared_months()` folds a closed month's event files
    into an archive file the moment a `TokenTracker` is constructed for
    that professor, mirroring the existing month-rollover check for local
    mode.
  - New module-level, mode-agnostic helpers, reused by both this module
    and `data/visualize_usage.py`: `fold_usage_records()` (build the
    standard month-summary shape from a flat list of call records),
    `load_usage_tree(base_dir)` (read one `data/`-shaped directory —
    mutable files, archives, and/or event files — into `{professor:
    {month: data}}`), and `get_configured_data_roots()` (this
    installation's local `data/` plus every configured external source).
  - `TokenUsage` gained a `source: str = ""` field, populated on every
    call (local mode included) from the installation's configured source
    id — see the migration section below for why local mode also tags it
    now, not just shared-write.
- **`src/cli.py` / `src/runtime/info_commands.py`** — added `usage
  sources list/add/remove` to the existing built-in `usage` command tree.
  The interactive `add` flow prints the exact command to run on the other
  installation for a `shared-write` source. One naming note: the flag for
  "which professor this source is for" is `--for-professor` (not
  `--professor`), because `--professor` would have collided with the
  parser's existing top-level positional `professor` argument (both would
  try to set the same `args.professor` slot).
- **`data/visualize_usage.py`** — `load_all_data()` now calls
  `get_configured_data_roots()` + `load_usage_tree()` instead of scanning
  only its own directory; later roots override earlier ones for the same
  professor+month (local root is always first, so an external source
  naturally wins over stale local leftovers). Added a fifth chart, "cost
  by source," built from the `source` tag on `session_history` entries.
- **`usage report --all-time`** (CLI, per-professor) needed no changes —
  it already asks "how much has this professor spent," and the new read
  path is fully transparent underneath the same `TokenTracker` methods.

### Making this easy to set up — Status: **built**

```
python main.py heller usage sources list
python main.py heller usage sources add
python main.py heller usage sources remove <label>
```

`usage sources add` prompts for anything not already passed as a flag
(`--label`, `--path`, `--mode`, `--for-professor`); defaults the source id
to the machine's hostname; warns (doesn't block) if the path doesn't exist
yet; and for `shared-write` prints the ready-to-run command for the other
installation, e.g.:

```
Add this on the other installation so both sides see each other's activity:

    python main.py smith usage sources add \
        --label "This installation" \
        --path "/Users/heller/Dropbox/Smith-Shared/data" \
        --mode shared-write \
        --for-professor smith
```

### One-time migration — Status: **built**

`scripts/migrate_usage_records.py` works as designed: backs up `data/` to
`data/_pre_migration_backup/` (skipped, not overwritten, if a backup
already exists — makes re-running safe), backfills `source` onto every
`session_history` record missing it across both active and archived
files, and supports `--dry-run`. Verified against this project's own real
`data/` folder during implementation (3 real professors, several archived
months) with no errors.

### Verification note

This was implemented and manually verified end-to-end (source_config
CRUD, `fold_usage_records`, `load_usage_tree` against both synthetic and
this project's real `data/` folder, `TokenTracker` in shared-write mode
including two simulated "installations" writing concurrently and a
closed-month rollover, the full `usage sources` CLI dispatch path, and the
migration script including its backup/idempotent-rerun behavior) — but
**not yet run through the actual `pytest` suite**, since this sandbox has
no network access to install this project's dependencies (`portkey-ai`,
`openai`, `pytest` itself) and the repo's own `.venv` is a macOS-specific
symlink that doesn't resolve here. Tests were still written
(`tests/test_source_config.py`, `tests/test_token_tracker_shared.py`,
`tests/test_migrate_usage_records.py`, plus additions to
`tests/test_token_tracker.py`, `tests/test_info_commands.py`,
`tests/test_cli_parser.py`) and one real bug was caught and fixed via the
manual verification (`scripts/migrate_usage_records.py` crashed computing
a display path when `DATA_DIR` was redirected outside the repo root, which
every test that redirects `DATA_DIR` does) — but you should run `pytest`
for real on your machine before merging, since manual spot-checks can't
fully substitute for it.

### One-time migration — no backward compatibility carried forward

Introducing the `source` tag (§1 above) means every `TokenUsage` record
going forward has a field your existing `data/token_usage_*.json` and
`data/archives/*/*.json` files don't have. The tempting-but-wrong move is
to make every reader in `TokenTracker`/`load_usage_tree()` tolerate a
missing `source` (`record.get("source", "unknown")` scattered everywhere)
so old files keep working forever. Since you're the only user of this
data right now, there's a much better option: convert what already
exists, once, and never write that tolerance code at all.

**`scripts/migrate_usage_records.py`** (standalone, not a permanent CLI
subcommand — run once, then it's just sitting there unused, which is
exactly the "becomes dead code" outcome you're after rather than
something that has to be maintained):

1. Copies `data/` to `data/_pre_migration_backup/` before touching
   anything (cheap insurance against a bug in the script itself — separate
   concern from format backward-compatibility, just don't want a one-time
   script to be the thing that loses real billing history).
2. Walks every `token_usage_*.json` and `archives/*/*.json` file and adds
   `"source": <your configured source_id, or the machine hostname if
   unset>` to every record in `session_history` that doesn't already have
   one. Nothing else about the file shape changes.
3. Prints a summary (files touched, records updated) and exits — no
   ongoing state, no flag left behind saying "migration has run," because
   after this point every reader in the codebase is written to assume the
   field is simply always present. There's nothing left to check.

With that run, `TokenTracker`, `load_usage_tree()`, and every aggregation
helper in this plan are written against one schema, full stop — no
version checks, no `.get()` fallbacks, no "if this is an old-style file"
branches anywhere in `src/`. If a professor is later switched to
`shared-write` mode (§1's `usage sources add`) and already has local
current-month activity, that same switch converts *their* still-open
month's records into initial event files at that point too, so turning
sharing on doesn't orphan anything already recorded — a smaller version of
the same one-time-conversion idea, triggered on demand instead of run
globally upfront.

---

## 2. How the web UI fits the existing architecture (revised)

Corrected model: **one person, one running instance, possibly several
professor configurations underneath it** (exactly like the CLI already
works — `python main.py heller ...` vs `python main.py smith ...` today).
The web UI doesn't need per-professor login identity; it needs a
**professor switcher** — pick which professor's API key/budget/
conversations are "active" right now, the same choice the CLI's positional
`professor` argument already makes per invocation.

Every other command in this project is **one-shot**: parse one command,
build one `SandboxProcessor`, make one (or a batch of) API calls, exit.
The web UI breaks that — `python main.py webui serve` starts a
*long-running process*. The resolution is the same as before: the
plugin's `run()` only starts the Uvicorn server; every chat turn or usage
lookup after that happens inside a FastAPI route handler that directly
builds a `SandboxProcessor` for whichever professor is currently selected
in the browser session — the same object every plugin already builds,
just invoked per HTTP request instead of per CLI invocation.

```
Browser  ──HTTP/SSE──►  FastAPI route (plugins/webui/src/routes/chat.py)
                              │  (professor = whichever one is selected
                              │   in this browser tab's session)
                              ▼
                    SandboxProcessor(professor_safe_name, model=...)
                              │
                              ▼
                    sandbox.chat_service   (lazily wired, same __getattr__
                                             mechanism every other plugin uses)
                              │
                              ▼
                    BaseService._create_completion(..., stream=True)
                              │
                              ▼
                    TokenTracker.record_usage(...)   ← same file-based
                                                         per-professor tracking
```

`src/cli.py::_dispatch()` needed a small change because it currently
raises `"Professor name is required"` before even looking at which
command was requested (see §5) — and, as it turned out once this was
actually built and tested end-to-end, argparse itself also needed a small
assist: see the deviation note near the top of this document for why
`python main.py webui serve` doesn't parse correctly on its own the way
this section originally assumed.

---

## 3. New plugin: `plugins/webui/`

Status: **built**, with one deliberate structural simplification from the
layout originally sketched here — routes live inside `app.py` (as inline
route functions, organized with comments rather than separate files) and
auth is one file instead of an `auth/` package, because both changes
minimize how many of this plugin's own internal files need to import each
other, which — as covered in the deviation note near the top of this
document — is more constrained in this project than an ordinary Python
package would be. Functionally nothing from the original sketch was
dropped; `memory_store.py`/`static/` remain not-yet-built (phase 2 and
"not needed yet" respectively — the current templates keep their CSS/JS
inline).

```
plugins/webui/
├── plugin.py                       # commands=["webui"], requires_professor=False; wires everything together
├── settings.toml                   # host/port defaults, compaction thresholds, session cookie name
├── src/
│   ├── settings.py                 # registered as pu_plugin.webui.settings — same walk-up pattern as translation
│   ├── app.py                      # FastAPI app factory + every route (registered as flat name _pu_webui_app — see its own docstring)
│   ├── auth.py                     # AuthBackend protocol + PassphraseBackend (registered as _pu_webui_auth); CasBackend still just documented, §4
│   ├── conversation.py             # Message/Conversation dataclasses + ConversationStore (registered as _pu_webui_conversation)
│   ├── services/
│   │   └── chat_service.py         # registered as src.services.chat_service (real convention); ChatService(BaseService)
│   └── templates/
│       ├── unlock.html             # Jinja2, inline CSS
│       └── chat.html               # Jinja2, inline CSS/JS — professor switcher, conversations, model field, spend sidebar
└── tests/
    ├── conftest.py                 # registers every module above into sys.modules, mirroring other plugins' conftest.py
    ├── test_plugin.py
    ├── test_auth.py
    ├── test_conversation.py
    ├── test_chat_service.py
    └── test_app.py                 # FastAPI TestClient integration tests
```

Not yet built: `memory_store.py` (phase 2, §6), `conversation_compaction`
logic (§6/§9 step 10), streaming (§9 step 9), and a `static/` directory
(not needed yet since CSS/JS is inline in the two templates — may be split
out later if it grows).

`plugin.py` follows the same contract every plugin uses, with one addition
(the `requires_professor` flag from §5):

```python
class WebUiPlugin:
    commands: list[str] = ["webui"]
    requires_professor: bool = False   # new, optional — see §5

    def register_subparsers(self, subparsers):
        p = subparsers.add_parser("webui", help="Run the local web interface")
        webui_sub = p.add_subparsers(dest="webui_subcommand")

        serve = webui_sub.add_parser("serve", help="Start the web server")
        serve.add_argument("--host", default="127.0.0.1")
        serve.add_argument("--port", type=int, default=8000)

        set_pp = webui_sub.add_parser("set-passphrase", help="Set the local unlock passphrase")

    def run(self, args, professor, model, temperature, top_p, max_tokens):
        if args.webui_subcommand == "set-passphrase":
            _print_passphrase_hash()   # prompts via getpass, prints the .env line to paste in
        else:
            # NOT a real relative import — see the deviation note near the
            # top of this document for why. The actual code fetches the
            # already-registered app module via sys.modules["_pu_webui_app"].
            app_module = sys.modules["_pu_webui_app"]
            app_module.run_server(host=args.host, port=args.port)   # blocks — long-running process
```

`webui set-passphrase` prompts for a passphrase (hidden input via
`getpass`) and prints a `WEBUI_PASSPHRASE_HASH=...` line for you to paste
into `.env` by hand — it doesn't write `.env` automatically, since that
file also holds live API keys.

---

## 4. Auth — one unlock gate, pluggable backend

Still no per-professor login identity at the HTTP layer — that question is
fully handled by the in-app professor switcher (§2), independent of who or
what unlocks the app itself. What changed: the gate itself is built behind
a small `AuthBackend` interface from the start, so it isn't a passphrase
hard-coded into the request path — it's whichever backend is configured,
same idea as the original draft's per-professor auth abstraction, just
scoped down to "does this one request get past the front door" instead of
"which professor is this."

```python
# plugins/webui/src/auth.py
class AuthBackend(Protocol):
    async def authenticate(self, request: Request) -> bool: ...
```

**`PassphraseBackend`** (built now) — checks a submitted passphrase
against `WEBUI_PASSPHRASE_HASH` in `.env` (bcrypt, generated by
`webui set-passphrase`). Empty/unset = no gate, useful for a strictly
`127.0.0.1`-only setup. This is what ships first.

Uses the `bcrypt` package directly rather than going through `passlib` as
originally sketched — found while testing that `passlib`'s bcrypt wrapper
is unmaintained and breaks outright against `bcrypt>=5.0.0` (it removed the
`__about__` attribute passlib's version-detection probes for, and the
underlying algorithm now raises instead of silently truncating passwords
past 72 bytes). The `bcrypt` package's own `hashpw()`/`checkpw()` don't
have either problem; `auth.py` truncates to 72 bytes itself before hashing
to keep behavior predictable regardless of input length.

**`CasBackend`** (documented here, not built yet) — authenticates against
Princeton's Central Authentication Service instead of a passphrase.
Same `AuthBackend` contract, so nothing in routing, sessions, or the
professor switcher needs to change when it's added later — it's a drop-in
second implementation, selected via a `[webui] auth_backend = "cas"`
setting.

Why this is worth having even in a single-unlock-gate design, per your
reasoning: it's the same login the professor already uses for every other
Princeton service, so there's no separate passphrase to create or lose,
and it means this tool authenticates the same way OIT already expects
university-facing tools to — rather than a bespoke passphrase that's
outside whatever standard they hold other services to.

What it actually needs when it's built (unchanged from the earlier
analysis, still true):
- Princeton OIT has to register the service URL before CAS will redirect
  back to it, and CAS requires a real HTTPS hostname — it won't hand
  tickets to `localhost` or plain HTTP. `webui serve` would need to be
  reachable at a real domain with a TLS certificate by then (§8), not
  just running on a laptop.
- The protocol itself (redirect → ticket → server-side validation →
  NetID) is small.
- Because there's no per-professor identity to map to anymore, it's
  simpler than the original design: no `PROF_N_NETID` field needed. Just
  an authorization check — is the NetID CAS hands back allowed to unlock
  this installation at all? — via a new optional `.env` field (e.g.
  `WEBUI_AUTHORIZED_NETIDS=jheller,asmith`) checked once at login. That
  field isn't needed until `CasBackend` is actually built, so it's not in
  the "changes required now" table in §5 — noting it here so the shape is
  on record for when it is.

Session handling is unchanged either way: a signed cookie (Starlette's
session middleware) holding just `{"unlocked": true, "active_professor":
"heller"}` — no secrets in the cookie, and nothing backend-specific in it.

---

## 5. Changes required outside `plugins/`

| File | Change | Why |
|---|---|---|
| `src/runtime/plugin.py` | Document an **optional** `requires_professor: bool` attribute (defaults to `True` via `getattr`, so every existing plugin is unaffected) | Lets a plugin declare its command isn't scoped to one professor at CLI-invocation time — **built** |
| `src/cli.py` (`_dispatch`) | Before raising `"Professor name is required"`, check `getattr(plugins.get(args.command), "requires_professor", True)`; if `False`, allow `professor=None` through to `plugin.run()` | Today the professor check fires unconditionally before the command is even looked at — **built** |
| `src/cli.py` (`main`) | Added `_insert_professor_placeholder_if_needed()`, called before `parser.parse_args()` | Works around a real argparse limitation found while testing: `python main.py webui serve` (2+ tokens) doesn't parse correctly without it, even though `python main.py webui` (1 token) does — see the deviation note near the top of this document — **built** |
| `src/tracking/token_tracker.py` | Add the `shared-write` event-file recording/reading path alongside today's single-mutable-file path (default, unchanged); add `load_usage_tree()` and `get_configured_data_roots()`; add closed-month event-file rollover (§1) | External-source aggregation and safe multi-writer usage tracking over a synced folder like Dropbox |
| `src/settings.py` | Expose `SOURCE_ID`, `EXTERNAL_SOURCES` from the new `[storage]` section | Same pattern as every other constant here |
| `settings.toml` | Add a commented-out `[storage]` section documenting the shape | Discoverability without committing anyone's real path |
| `data/visualize_usage.py` | Replace its own `load_all_data()` with the shared `token_tracker` functions; add the per-`source` activity breakdown to its charts | Stop duplicating the merge logic once it needs to span multiple roots and two write modes |
| `scripts/migrate_usage_records.py` *(new file, run once)* | Backfills the new `source` field onto every existing record, with an automatic backup first | Lets every reader in `src/` assume one schema, with no legacy-format branches — run once, then it's inert (§1) |
| `.env.template` | Document the new optional `WEBUI_PASSPHRASE_HASH` field | Keep the template in sync |
| `src/models/catalog.py` | Add two optional per-model fields (`supports_streaming`, default `True`; `context_window`, integer) and matching getters `model_supports_streaming()` / `get_model_context_window()`, following the existing `supports_vision`/`fixed_parameters` pattern | Controls streaming fallback and compaction threshold; backward-compatible |
| `src/services/base_service.py` | Add a streaming variant of `_create_completion()` (`stream=True, stream_options={"include_usage": True}`) and a usage-recording path that accumulates the final chunk's usage instead of a full response object | Generic capability, reusable by any future plugin |
| `requirements.txt` | Add `fastapi`, `uvicorn[standard]`, `jinja2`, `python-multipart`, `bcrypt`, `itsdangerous`, `httpx2` (tests) | New runtime/test dependencies — `bcrypt` directly rather than `passlib[bcrypt]` as first sketched (see §4's deviation note); `httpx2` rather than `httpx` since Starlette's `TestClient` deprecated plain `httpx` |
| `pytest.ini` | Add `plugins/webui/tests` to `testpaths` | Same as every other bundled plugin |
| `pyrightconfig.json` | No change planned — leave `plugins/webui/src/` out of the ignore list, matching `prompt/`'s precedent | Hold the new plugin to the same bar; revisit only if FastAPI's typing proves noisy |
| `docs/architecture.md`, `docs/plugin-authoring-guide.md` | Document `requires_professor`, the `[storage]` settings, and the "long-running server" plugin shape once built | Keep docs in sync |

Everything else — `SandboxProcessor`, lazy `__getattr__` service wiring,
`resolve_model()`'s `provider/model` auto-registration, `FileOutputHandler`,
the processors — is reused as-is. "Faculty can request a new model by
typing `anthropic/claude-fable-5`" is **already implemented** for the
CLI's `-m` flag; the web UI's model picker just needs a free-text field
passed straight through to `SandboxProcessor(model=...)`. One thing worth
knowing either way: `model_catalog.json` is a single shared file — a model
one professor auto-registers becomes available to everyone, same as `-m
provider/model` already behaves on the CLI today.

---

## 6. Data model — conversations, compaction, memory

Unchanged in shape from the first draft, just re-scoped: "professor" here
means whichever one is currently selected in the switcher, not a logged-in
identity.

```
data/
├── conversations/
│   └── {professor_safe_name}/
│       └── {conversation_id}.json
└── webui_memory/
    └── {professor_safe_name}.json      # phase 2
```

```json
{
  "id": "c_8f2a...",
  "title": "Revising the Meiji land-tax chapter",
  "created_at": "2026-07-24T10:03:00",
  "updated_at": "2026-07-24T10:14:22",
  "model": "gpt-4o",
  "messages": [
    {"role": "user", "content": "...", "timestamp": "...", "model": null},
    {"role": "assistant", "content": "...", "timestamp": "...", "model": "gpt-4o",
     "prompt_tokens": 812, "completion_tokens": 340, "cost": 0.0057}
  ],
  "compacted_summary": null
}
```

**Compaction**: after every turn, `ChatService` checks the `prompt_tokens`
figure the API just reported (already captured for billing — no separate
tokenizer library needed) against a per-model `context_window` value
(§5). Past a configurable threshold (default 70%, in
`plugins/webui/settings.toml`), the *next* turn first makes a cheap
summarization call (e.g. `gpt-4o-mini`) that condenses the oldest messages
into `compacted_summary`, which is what's sent to the model going forward
— the full raw transcript stays on disk. The summarization call is billed
and tracked like any other call.

**Persistent memory** (phase 2): a small per-professor notes file
injected into every new conversation's system prompt. Manually edited in
v1; auto-updating from conversation content is a later enhancement.

---

## 7. Frontend

Server-rendered shell (Jinja2: unlock page, chat page skeleton) plus plain
JavaScript for the interactive parts — professor switcher, conversation
list, message stream via `EventSource`, model picker, spend sidebar with a
hide/show toggle. No Node/webpack build step, consistent with this being a
pure-Python project.

The spend sidebar calls `GET /api/usage` for the active professor (current
month + historical, via the same `TokenTracker`/`load_usage_tree()`
functions from §1) and also gets a live update pushed as the final event
of each chat turn's SSE stream. If external sources are configured, an
optional "combined" toggle can show the same all-installations view
`data/visualize_usage.py` produces — useful if you're the one operating
this on behalf of several professors and want the aggregate picture
without leaving the chat UI.

---

## 8. Deployment notes

- The first part of this project that runs as a **persistent service**
  rather than a one-shot CLI call — run it under something that restarts
  it on crash/reboot (systemd unit, `supervisord`, etc.) if you want it
  always available, not just `python main.py webui serve` in a terminal.
- Binding to `127.0.0.1` (the default) means only your own machine can
  reach it — no auth gate strictly needed in that mode. If you ever want
  to reach it from another device (e.g. your phone on the same network,
  or a professor accessing it directly rather than through you), that's
  the point at which the auth gate actually matters and a
  TLS-terminating reverse proxy (nginx/Caddy) in front becomes worth
  doing — required outright once `CasBackend` is added (§4), since CAS
  won't work over plain HTTP at all, and good practice even with the
  passphrase backend so credentials aren't sent over the network in the
  clear.

---

## 9. Suggested build order

1. ~~External data sources (§1)~~ — **done**, on `feature/webui-and-remote-sources`.
2. ~~Run `scripts/migrate_usage_records.py` once against your real `data/`
   folder~~ — **done** (checked 2026-07-24; nothing needed converting, see
   the status note at the top of this document).
3. ~~Core plumbing: `requires_professor` in `cli.py`/`plugin.py`, empty
   `plugins/webui/` skeleton serving a "hello" FastAPI app on
   `webui serve`.~~ — **done.**
4. ~~Auth gate (`AuthBackend` + `PassphraseBackend`) + professor switcher.~~ — **done.**
5. ~~`ChatService` (non-streaming first) + conversation store + basic chat
   page.~~ — **done.**
6. ~~Model picker wired to the existing catalog/`resolve_model()`, including
   free-text `provider/model` requests.~~ — **done** (the model field in
   the chat page is free text with a datalist of catalog names; any text
   typed there is passed straight through to `SandboxProcessor(model=...)`,
   the same auto-registration path the CLI's `-m` flag already uses).
7. ~~Spend sidebar (`/api/usage`, active professor, current month) + hide/
   show toggle.~~ — **done.**
8. Historical + combined-sources usage in the sidebar.
9. Streaming responses (SSE) + the `supports_streaming` catalog flag +
   fallback path.
10. Compaction (context-window tracking + summarization call).
11. `CasBackend` as a second `AuthBackend` implementation (§4), once a
    hostname/TLS/OIT service-URL registration is in place — no other
    part of the app needs to change to support it.
12. Memory notes (phase 2).

### Verification note (sections 2-7)

Initial build had the same sandbox limitation as section 1 (no network
access here to install `fastapi`/`uvicorn`/`jinja2`/etc.), so first-pass
verification was a structural simulation — lightweight stand-ins for the
missing third-party packages driving the *actual* `load_plugins()`,
`create_argument_parser()`, and `_dispatch()` functions from this codebase
against the real `plugins/webui/plugin.py`. That caught the argparse bug
above but couldn't exercise real FastAPI routing or Jinja2 rendering.

The real `pytest` run (on your machine, with actual dependencies installed)
then caught three more real bugs, all now fixed:

1. **Test collection collision.** `plugins/webui/tests/test_plugin.py`
   collided with `plugins/prompt/tests/test_plugin.py` — pytest's default
   import mode can't have two same-named test modules without `__init__.py`
   files disambiguating them. Renamed to `test_webui_plugin.py` rather than
   adding `__init__.py` everywhere, to keep the fix scoped to the file that
   introduced the collision.
2. **`TemplateResponse` signature.** The installed Starlette version has
   fully removed the old `TemplateResponse(name, {"request": request, ...})`
   calling convention (deprecated for a while, apparently gone now) in
   favor of `TemplateResponse(request, name, context)` — `request` as an
   explicit first argument, not folded into the context dict. `app.py`'s
   three template calls were written against the old form and needed
   updating.
3. **`passlib`'s bcrypt wrapper is broken against current `bcrypt`.**
   `passlib` (unmaintained) does an internal self-test against the
   installed `bcrypt` package on first use; `bcrypt>=5.0.0` removed the
   attribute that self-test reads (`AttributeError: module 'bcrypt' has no
   attribute '__about__'`) and also stopped silently truncating passwords
   over 72 bytes the way `passlib` expects, surfacing as a
   `ValueError: password cannot be longer than 72 bytes` even for short,
   ordinary passphrases — the 72-byte string passlib was actually hashing
   was its own internal bug-detection probe, not anything a person typed.
   Fixed by dropping `passlib` and using the `bcrypt` package directly in
   `auth.py` (see the deviation note in §4) — this also removes a
   dependency on an unmaintained wrapper going forward.

All three fixes address every failure/error pytest reported (10 failed, 13
errored, all traced to one of the three causes above) — not yet
re-confirmed with a clean run at time of writing.

Each step is independently testable and shippable.

---

## 10. Plugin actions in the composer (replaces the generic attachment icon)

Status (2026-07-25): **built end-to-end** — backend, the composer's
puzzle-piece button, the action picker, and the two-pane live prompt
preview panel are all in place and wired together. (Earlier the same day,
this status line said "backend built and tested, front end not yet
built" — that was true for a few hours, until the front-end pass below.)

**Second pass, same day, after manual testing surfaced a real bug and more
requests came in:**
- **Silent job-failure fix**: `jobs.py`'s success path (append `job_result`,
  clear `active_job_id`, save) had no exception handling at all, unlike the
  failure path right next to it. Any error while *recording* an
  already-successful job's result (not the job itself failing) left
  `active_job_id` set forever with no result and no error ever shown — a
  plausible explanation for "results aren't showing up." Now wrapped, with
  a `job_error` fallback so the conversation always unlocks either way.
  Covered by a new regression test in `plugins/webui/tests/test_jobs.py`
  that simulates the save failing.
- **Temperature/top-p/max-tokens**, end to end: `Conversation` gained
  `temperature`/`top_p`/`max_tokens` (persisted per-conversation like
  `model`), `POST /api/chat` applies them, `GET /api/models` now reports
  `accepts_sampling_params` per model (`model_has_fixed_parameters()`/
  `model_omit_sampling_params()` from `src/models/catalog.py`) so the front
  end hides temperature/top-p entirely for a model that would reject them
  rather than showing controls that fail at submit time. A small "tune"
  icon next to the model picker exposes these for ordinary chat; the job
  modal gets the same three fields appended under a "Sampling" group,
  built as synthetic `UiField`s (`samplingFieldsFor()` in `chat.html`) so
  they flow through the exact same render/collect/restore code as a
  plugin's own fields rather than needing special-casing.
- **Token spend on every reply**: chat turns already carried
  `prompt_tokens`/`completion_tokens`/`cost` on `Message`, just never
  rendered the token counts (only cost). Now shown — `"gpt-4o · $0.02 ·
  1200 in / 340 out tokens"` — so a person can tell a reply used its full
  response budget, not just what it cost. Job results (translate/
  transcribe) now report this too: `TokenTracker` gained
  `get_session_usage()`, a running total scoped to *one instance's*
  lifetime (one CLI run, or one webui request/job — see
  `SandboxProcessor.__init__`), read by `run_ui_action` after the job
  finishes and threaded onto `UiJobResult`/the `job_result` `Message`.
- **Translate composer options expanded**: `output_format` (select:
  same-as-source/docx/pdf/txt/md — mirrors `-o`'s extension-driven choice,
  and now genuinely affects the live prompt preview via `build_prompts()`'s
  `output_format` kwarg), `preserve_tables`, `toc`, `preserve_media`,
  `font`, `font_size`, `workers`, `spread`. `UiField` gained an optional
  `group` (a cosmetic section-header string) and `choices` (for the new
  `'select'` kind) — every translate field now has a group so 14 fields
  reads as five short sections instead of one dense column.
- **Job modal polish**: increased field spacing, group headers, Escape
  now closes the modal (previously only the settings modal had this), a
  Reset button (circular double-arrow icon) next to Close that explicitly
  blanks the form, and — the flip side of Reset existing — closing via ×,
  Cancel, or Escape now **preserves** whatever was typed (`state.jobFieldValues`,
  keyed by action id) instead of wiping it, restored the next time that
  same action's modal reopens. A file selection is restored too, via
  `DataTransfer` (falls back to leaving the file field empty in browsers
  that don't support it).

**Resolved, same day:** asked whether to generalize the composer so
extension plugins (Kanbun on `translation-ea`, etc.) can contribute their
own fields. Your call: a subsection appearing right after the base
plugin's own options, refreshing whenever the destination language is
picked — since that's the same moment `get_peer_guidance()` already kicks
in for an extension plugin. Built as a plain, decoupled global registry in
`src/runtime/ui_action.py` (`ExtensionUiHooks`/`register_extension_ui_hooks()`/
`get_extension_ui_fields()`/`apply_extension_ui_hooks()`) rather than
routed through `DispatchPlugin` — necessary because `run_ui_action` is
looked up directly off the primary plugin instance (`DispatchPlugin.__getattr__`
forwards it straight through, never calling anything on `DispatchPlugin`
itself), so it has no way to ask "which extension owns this destination
token" through `DispatchPlugin` the way `run()`'s `args._peer_guidance`
injection does. Mirrors `register_language()`'s existing pattern instead:
any plugin calls `register_extension_ui_hooks(token, fields, apply)` at
import time; `TranslationPlugin.run_ui_action`/`preview_ui_action` call
`apply_extension_ui_hooks(target_code, sandbox, fields)` right alongside
where `notes`/`preserve_tables` are already applied; a new
`GET /api/plugin-actions/{action_id}/extension-fields?target_language=...`
endpoint is what the composer polls on every destination-language change.
`plugins/translation/plugin.py`'s TEMPLATE GUIDE documents the exact call
an extension plugin author makes (step 8), with a worked Kanbun example.

**Important caveat**: `translation-ea` itself lives in a separate,
git-ignored repo not present in this checkout, so this was built and
tested against a *fake* registered extension (see
`TestExtensionUiHooksIntegration` in
`plugins/translation/tests/test_translation_plugin_ui_action.py` and
`TestExtensionUiHooksRegistry` in `tests/test_ui_action.py`). Kanbun itself
won't actually appear in the composer until `translation-ea`'s own
`plugin.py` adds the few lines shown in the template guide — that's a
follow-up in that other repo, not something finishable from here.

**Third pass, same day: per-page messages while a translation job runs.**
Report: "no messages from each page in the conversation while a
translation is going on — is console printing swallowing it?" Root cause,
confirmed by tracing the call chain: no, nothing was swallowed — the
capability never existed. `ProgressCallback` is `Callable[[int, int], None]`,
done/total counts only; the actual translated text for each page only ever
reached a `print()` call (gated off in the webui path via
`self._suppress_inline_print`) or a progressive-save file write. `jobs.py`'s
`on_progress` closure had no way to receive text because the interface
never carried any.

Fix: a new sibling callback, `PageTextCallback = Callable[[int, str], None]`
(`src/runtime/ui_action.py`), threaded alongside `on_progress` everywhere,
optional and defaulting to `None` so the CLI path and every existing caller
is unaffected — deliberately a new parameter rather than widening
`ProgressCallback` itself, which would have forced changes to every
existing 2-arg callback site (including CLI callers and test lambdas) for
no reason. Threaded through the same sequential-only path as
`on_progress`: `translation_service.py`'s `_translate_page_sequence()` (and
`translate_document()`/`translate_text_pages()` above it),
`document_handler.py`'s `_process_text_based_file()`/`translate_document()`/
`process_image_translation_folder()` (each range/segment computes its own
text-offset closure, mirroring the existing progress-offset ones, so page
numbers stay correct across a multi-range request like `-p "1-5,10-15"`),
`plugins/translation/plugin.py`'s `run_ui_action`, and `jobs.py`'s
`_run_job()`, which now appends a new `job_page` message
(`Message.kind == "job_page"`, `Message.page_number` set, 1-indexed) to the
conversation every time a page finishes. `plugins/transcription/plugin.py`
accepts `on_page_text` too (unused for now — transcription doesn't stream
per-image text yet).

Frontend (`chat.html`): `job_page` is the one job-message kind that renders
as an ordinary-looking bubble with a small "Page N" label above the text,
and — unlike `job_progress`/`job_result`/`job_error` — still gets the
copy/export action row, since it's real translated content someone may
want to reuse. `job_progress` is still fully suppressed from the
transcript (shown only in the progress bar under the composer).

Covered by new tests across `test_translation_service.py`
(`TestTranslateTextPagesPageText`), `test_document_handler.py` (including
the multi-range offset case), `test_translation_plugin_ui_action.py`,
`test_jobs.py`, and `test_conversation.py`.

**Follow-up, same day:** per-page streaming is sequential-only
(`workers == 1`) — with `workers > 1`, `translation_service.py` takes the
`_translate_pages_parallel` branch entirely, which never receives
`on_progress`/`on_page_text` at all, since pages can finish out of order
and there's no buffering to put them back in order before reporting them.
This mirrors a restriction the progress bar already had; not new to this
fix. Your call: rather than build out-of-order buffering (bigger, separate
piece of work) or show possibly-confusing out-of-order page bubbles, leave
the progress bar accurate (as it already was) and add one plain heads-up
instead. `jobs.py`'s `_run_job()` now checks the submitted `workers` field
before starting the job and, if greater than 1, appends a new `job_notice`
message: "Preview of the translation is turned off while running with more
than one worker. The progress bar below will still update normally, and
the finished document will be here as soon as the job completes." A
`job_notice` renders as a plain, subdued bubble (italic, muted, no
copy/export row — it's an aside, not content) and is excluded from
`api_messages()`/`display_messages()` like every other job-kind message.
Not every plugin declares a `workers` field (transcription doesn't), so
this is a no-op wherever it's absent.

Full suite re-run clean after this pass: 1572 passed, 19 deselected
(`-m "not live"`, network-dependent pricing tests), one pre-existing
unrelated failure
(`test_model_catalog.py::TestResolveModel::test_provider_model_not_in_catalog_auto_registers`).

**Fourth pass, same day: bring transcribe's composer up to parity with its own CLI options,**
before moving on to Kanbun. Checked what `transcribe`'s actual CLI surface
is (`plugins/transcription/plugin.py`'s `register_subparsers()`): just
`language_code`, `-i/--input`, the shared common flags (`-o`/`-m`/`-t`/`-T`/
`-M`/`--dry-run`), and notes flags — much thinner than translate's. Notably,
`vertical`/`spread`/`passes`/`workers` are **not** base-CLI flags at all for
transcribe; `process_image`/`process_image_folder` in
`plugins/transcription/src/runtime/image_handler.py` accept those
parameters, but only `transcription-ea`'s `register_command_flags()` ever
sets them via a CLI flag (the base `run()` always calls with the defaults).
Those stay out of the base composer for the same reason Kanbun's fields
stay out of translate's base composer — they belong behind the same
extension-field registry mechanism, as a follow-up once transcription-ea
itself is touched, not duplicated into the base plugin now.

What *is* a genuine, currently-missing base-CLI capability: `-o result.docx`
vs. `-o result.pdf` vs. `-o result.txt` already picks the output writer by
extension (same `save_translation_output()` translate uses), but the webui
composer hardcoded `.txt` regardless. Added an `output_format` select field
(txt/docx/pdf/md — same choices as translate's, minus its "same as source"
option, since an image has no source-format extension to match), grouped
alongside `target_language`/`file` under "Document" and "Output" section
headers mirroring translate's now-established grouping convention.
`run_ui_action` reads it and picks the output extension the same way
translate's does. Covered by new tests in
`test_transcription_plugin_ui_action.py`: format-to-extension mapping for
all four choices, default-to-txt when omitted, and fallback-to-txt for an
unrecognized value.

Full suite re-run clean: 1579 passed, 19 deselected, the same one
pre-existing unrelated failure.

**Fifth pass, same day: let the composer's file field accept a folder,**
not just one file — asked right after the transcribe parity pass above,
since ``transcribe -i some_folder/`` already processes every image inside
it (``process_image_folder``), but the webui's file input only ever let a
professor attach one file, and ``/api/jobs`` only ever accepted one
multipart upload. Added ``UiField.allow_folder`` (default ``False``, so
translate's own single-document field is unaffected) — when set, the
composer's file `<input>` gets both `multiple` and `webkitdirectory`
attributes: in Chrome/Edge (which support the latter) this becomes a real
"choose a folder" picker; everywhere else `webkitdirectory` is silently
ignored and it falls back to an ordinary multi-file picker, still strictly
better than single-file-only. Set `allow_folder=True` on transcribe's
`file` field only (translate's own document field stays single-file for
now — a folder-of-page-images upload for translate's own `-i`-as-folder
CLI path isn't wired into `run_ui_action` at all yet, a separate,
not-yet-done piece of work, distinct from this).

`plugins/webui/src/app.py`'s `/api/jobs` route now takes a `files: list[UploadFile]`
form field (renamed from the old singular `file`) instead of one optional
upload: exactly one file keeps the previous behavior (saved directly,
`fields['file_path']` points at it); more than one file are all saved into
one new `input/` subdirectory, and `fields['file_path']` points at *that
directory* instead — exactly the shape `TranscriptionPlugin.run_ui_action`
already expected from a CLI user's folder path, so no plugin-side change
was needed there at all. `chat.html`'s `collectJobFieldValues()` now
returns an array of files (usually length 1) instead of a single `File`;
`restoreJobFieldValues()`/`startJobFromModal()` updated to match.

Covered by new tests: `tests/test_ui_action.py` (`allow_folder` defaults
false / can be enabled), `plugins/transcription/tests/test_transcription_plugin_ui_action.py`
(file field has `allow_folder=True`), and `plugins/webui/tests/test_app.py`
(single file unchanged, multiple files land in one folder with the right
`file_name`/`file_path`, zero files leaves `fields` untouched). Full suite
re-run clean: 1584 passed, 19 deselected, the same one pre-existing
unrelated failure.

**Sixth pass, same day: workers + PageTextCallback for transcribe, matching
translate's own.** Added a `workers` `UiField` (Performance group) to
transcribe's composer and threaded it through `run_ui_action` into
`sandbox.process_image_folder(..., workers=...)` — the underlying
parameter already existed there (only `transcription-ea` ever set it via
its own CLI flag; see below), so this is purely composer + `run_ui_action`
wiring. Added `PageTextCallback` support to
`plugins/transcription/src/runtime/image_handler.py`'s
`process_image_folder` (sequential path only, same restriction as
`on_progress`), mirroring `process_image_translation_folder`'s existing
behavior in the translation plugin exactly: called once per image, even
for an image whose own OCR call raised (with that image's error
placeholder text), so a professor watching the conversation sees every
image accounted for rather than a silent gap. `jobs.py`'s `on_page_text`
closure and the `job_notice` "preview disabled above 1 worker" message
were already plugin-agnostic (they just read the submitted `fields`), so
both now fire correctly for a multi-worker transcribe job with zero
jobs.py changes needed beyond rewording the notice message itself — it
previously said "the translation" by name, which would have been wrong
for a transcribe job; reworded to "each item"/"the finished result" so
it reads correctly for either plugin.

**Resolved, same day:** asked whether to add a bare `-w`/`--workers` CLI
flag to the base `transcribe` command too (translate already has one;
transcribe didn't) given `transcription-ea`'s `register_command_flags()`
already registered its own `-w`/`--workers` directly on the shared
`transcribe` parser — adding the same flag to the base plugin would make
`argparse` see the same option string twice the moment both plugins are
installed together, crashing CLI startup (`ArgumentError: argument
-w/--workers: conflicting option string(s)`). Translate never hit this
because `translation-ea`'s own `register_command_flags()` never touches
`workers` — the base translate plugin already owned it. Your call: "the
workers had no business being in transcription-ea," with explicit
permission to edit that separate repo and commit there directly (both
normally out of scope — see CLAUDE.md's "reference only" convention for
the `-ea` repos, and the usual "don't run git commit" rule).

Added `-w`/`--workers` to the base plugin's `register_subparsers()` (same
style as translate's: `type=int, default=DEFAULT_PARALLEL_WORKERS`) and
threaded it through `run()`'s `transcribe` branch into
`process_image_folder(..., workers=workers)`. Then, in
`plugins/transcription-ea` (a separate repo, cloned at
`plugins/transcription-ea/`): removed its now-redundant `-w`/`--workers`
registration from `register_command_flags()` only — left its standalone
fallback `register_subparsers()` (used only when the base plugin isn't
installed at all) unchanged, since that fallback already duplicates every
other base-plugin flag (`-i`, common flags, notes flags) for that
unsupported-but-handled scenario, not just extension-specific ones.
Updated that repo's module/class docstrings to explain the new split, and
committed there directly (`fix(cli): remove redundant -w/--workers, now
owned by the base plugin`) per your explicit go-ahead. Its own 30-test
suite re-run clean, including the one test that loads both plugins
together via the real `load_plugins()`/`create_argument_parser()` path —
exactly the scenario that would have raised the conflict directly.

`plugins/transcription/tests/test_transcription_cli.py` updated to match:
removed the old "workers is an extension-only flag, must not exist on the
base plugin" assertions, added coverage that `-w`/`--workers` now parses
correctly (long/short flag, default value, rejects non-numeric input) on
the base plugin alone.

Covered by new tests: `plugins/transcription/tests/test_image_handler.py`
(`on_page_text` order/content, called-even-on-error mirroring
`on_progress`'s existing test, `None`-by-default, not called on the
parallel path) and `plugins/transcription/tests/test_transcription_plugin_ui_action.py`
(`workers`/`on_page_text` forwarded to `process_image_folder`, `workers`
defaults to 1, invalid/negative `workers` raise `CLIError`). Full suite
re-run clean: 1598 passed, 19 deselected, the same one pre-existing
unrelated failure.

The generic "attach a document to chat" icon and its upload wiring were
already **removed** from `chat.html` in an earlier pass, before this
backend work started. Reasoning, unchanged: that icon extracted text from
a file and stuffed it into the chat context (`attachments.py`, still
present and unused, kept in case its text-extraction readers get reused
here) — a reasonable idea for "let the model read a short reference
document," but it had no connection to what `translate` and `transcribe`
actually do, which is a full document-processing job (page by page,
potentially minutes long, producing a formatted output file), not a chat
turn. Keeping one icon that vaguely did "something with a file" with no
visible options was worse than removing it and building the real thing.

Built this pass:
- `src/runtime/ui_action.py` (new) — `UiField`, `UiAction`, `UiJobResult`
  dataclasses, plus a `ProgressCallback` type alias. `src/runtime/plugin.py`
  documents the paired optional `ui_action`/`run_ui_action` attributes the
  same way it already documents `requires_professor` — absent by default,
  no existing plugin affected. Covered by `tests/test_ui_action.py`.
- **`on_progress` threaded through the translation plugin's execution
  path**, sequential (`workers == 1`) only, additive and optional
  everywhere so the CLI path is completely unaffected: `translation_service._translate_page_sequence()`
  (and `translate_document()`/`translate_text_pages()` above it) in
  `plugins/translation/src/services/translation_service.py`, and
  `_process_text_based_file()`/`translate_document()`/
  `process_image_translation_folder()` in
  `plugins/translation/src/runtime/document_handler.py` (the PDF branch
  and the text-file branch each compute their own running offset/total so
  progress is accurate even across more than one requested page range).
  Covered by new tests in `plugins/translation/tests/test_translation_service.py`
  (`TestTranslateTextPagesProgress`) and `test_document_handler.py`.
- **Same for the transcription plugin's `process_image_folder()`**
  (sequential path) in `plugins/transcription/src/runtime/image_handler.py`.
  Covered by new tests in `plugins/transcription/tests/test_image_handler.py`.
- **`translate`'s and `transcribe`'s `ui_action` + `run_ui_action`**,
  declared in `plugins/translation/plugin.py` and
  `plugins/transcription/plugin.py`. v1 field set, per your calls below:
  `translate` — source/target language, file, a `scanned` checkbox, page
  range, notes; `transcribe` — target language, file, notes.
  `transcription_review` is deliberately left out (see below). Both
  `run_ui_action` methods take a plain `fields` dict (not an
  `argparse.Namespace`) and an `output_dir` they must write their one
  output file into; `translate`'s also resolves an output extension
  (`.docx` only for a `.docx` source, `.txt` otherwise) and applies notes
  via the same `system_note`/`user_note` mechanism `-nb` already uses.
  Covered by `plugins/translation/tests/test_translation_plugin_ui_action.py`
  and `plugins/transcription/tests/test_transcription_plugin_ui_action.py`.
- **`plugins/webui/src/jobs.py`** (new) — `Job`/`JobStore` (in-memory,
  per your call), `find_plugin_for_action()`/`list_ui_actions()` (walk
  the same command-to-plugin dict `load_plugins()` already builds for the
  CLI), `start_job()` (validates, locks the conversation, launches a
  background thread), and `sweep_stale_jobs()` (the startup-sweep half of
  your in-memory-jobs call). Covered by `plugins/webui/tests/test_jobs.py`.
- **`Conversation`/`Message` model extended** (`plugins/webui/src/conversation.py`):
  `Conversation.active_job_id`; `Message.kind`
  (`'message'`/`'job_progress'`/`'job_result'`/`'job_error'`), `job_id`,
  `output_filename`, `output_path`. `api_messages()`/`display_messages()`
  now skip anything but `kind == 'message'` — a job status ping isn't
  dialogue for the model to reason over. Covered by new tests in
  `plugins/webui/tests/test_conversation.py` (`TestJobFields`).
- **`app.py` wiring**: `GET /api/plugin-actions` (lists every installed
  plugin's declared action), `POST /api/jobs` (multipart — professor,
  conversation id, action id, a JSON-encoded `fields_json` for the
  non-file values, and an optional file part saved straight into that
  job's own output directory before the job starts), `GET
  /api/conversations/{id}/job-outputs/{job_id}` (download — looks the
  file up server-side from the conversation's own `job_result` message
  rather than trusting a client-supplied path), `POST /api/chat` now
  returns `409` when the target conversation has an `active_job_id`, and
  `create_app()` runs `jobs.sweep_stale_jobs()` once at startup. Covered
  by new test classes in `plugins/webui/tests/test_app.py`
  (`TestPluginActions`, `TestStartJob`, `TestChatBlockedWhileJobRunning`,
  `TestJobOutputDownload`, `TestStartupSweep`).
- **One real bug found and fixed along the way**:
  `ConversationStore.save()` (`plugins/webui/src/conversation.py`) wasn't
  atomic — a plain `write_text()` truncates the file before writing the
  new content, so a `GET /api/conversations/{id}` landing in that window
  (now a real scenario, with a background job saving progress on its own
  thread while the browser polls) could read a partial, unparseable file.
  Fixed by writing to a temp file in the same directory and `os.replace()`-ing
  it into place, which is atomic on the same filesystem. Covered by a
  regression test in `test_conversation.py`
  (`test_save_is_atomic_no_reader_ever_sees_a_partial_file`) that
  deliberately races a writer thread against a reader thread.
- Full real `pytest` run (this environment had genuine network access and
  all dependencies installable, unlike the sandbox limitation noted
  earlier in this document) — 1469 passed, only one unrelated pre-existing
  failure (`test_provider_model_not_in_catalog_auto_registers`, present
  before this work started and untouched by it).

Built in the front-end pass (same day, after the backend above):
- **`src/runtime/ui_action.py`**: new `UiPromptPreview` dataclass
  (`system_prompt`, `user_prompt`, `model`, optional `note`) — the
  `--dry-run` idea made interactive. `src/runtime/plugin.py` documents the
  matching optional `preview_ui_action(fields, professor, model)` method,
  independent of the `ui_action`/`run_ui_action` pair (a plugin can decline
  a live preview even if it declares an action).
- **`preview_ui_action()` implemented for both `translate` and
  `transcribe`**, reusing each service's existing `build_prompts()` —
  exactly the reuse this design always called for. Deliberately lenient
  where `run_ui_action` is strict: called after every keystroke, so a
  blank/invalid language field falls back to a placeholder name instead of
  raising. Covered by `TestPreviewUiAction` in each plugin's
  `test_..._plugin_ui_action.py`.
- **`app.py`**: `GET /api/languages` (the registered `LANGUAGE_MAP`, for
  populating a `language`-kind `UiField` as a dropdown) and `POST
  /api/plugin-actions/{action_id}/preview` (calls `preview_ui_action`,
  returns `{"available": false}` — never a 500 — when a plugin doesn't
  implement it or raises). Covered by `TestLanguages` and
  `TestPluginActionPreview` in `test_app.py`.
- **`chat.html`**: the puzzle-piece button in the composer opens an action
  picker (from `GET /api/plugin-actions`); picking an action opens the
  two-pane job modal — options on the left (built generically off each
  `UiField.kind`: `language`→`<select>`, `file`→file input, `checkbox`,
  `text`→`<textarea>`), a live preview on the right (system prompt above,
  user prompt below, each independently scrolling), debounced 300ms after
  any field change. Starting a job posts to `/api/jobs`, then locks the
  composer (read-only, with the advisory placeholder text you asked for)
  and polls the conversation every 2s, rendering `job_progress`/
  `job_result` (with a download link)/`job_error` messages with their own
  bubble styling as they arrive, until `active_job_id` clears.
- **Three real bugs found by manually driving the actual `TestClient`
  end-to-end** (not caught by any unit test, since each one only exercised
  one layer in isolation) — the reason this pass included a manual
  smoke-test step before calling it done, not just a green `pytest` run:
  1. `ui_action` was assigned as a bare **module-level** variable in
     `plugins/translation/plugin.py` and `plugins/transcription/plugin.py`
     (`ui_action = UiAction(...)`), never attached to the `plugin` instance
     that `load_plugins()` actually returns. `jobs.py`'s
     `getattr(p, "ui_action", None)` reads it off that instance, so in a
     real run the composer's action list was always empty — every
     plugin-local test passed because they all read `plugin_module.ui_action`
     (the module) directly. Fixed with `plugin.ui_action = ui_action` at
     the bottom of both files; regression test added
     (`test_ui_action_is_reachable_from_the_plugin_instance`) plus a new
     integration test in `test_jobs.py` that loads the *real*
     `plugins/` directory the way `app.py` does.
  2. Once (1) was fixed, the action still didn't appear **in this specific
     installation**, because `translation-ea`/`transcription-ea` are
     installed here: `load_plugins()` wraps `translate`/`transcribe` in a
     `DispatchPlugin`, which had no `ui_action`/`run_ui_action`/
     `preview_ui_action` of its own at all. Fixed by giving
     `DispatchPlugin` a `__getattr__` that proxies exactly those three
     names to its primary (base) plugin — an extension plugin doesn't get
     its own separate composer entry; the base plugin's action already
     covers every language `register_language()` added globally. Covered
     by a new `TestUiActionPassthrough` class in `tests/test_dispatch_plugin.py`.
  3. With (1) and (2) both fixed, `GET /api/plugin-actions` listed
     `"transcribe"` **twice** — `transcribe` and `transcription_review`
     get wrapped in *two different* `DispatchPlugin` instances that both
     proxy to the same underlying action, and `list_ui_actions()` deduped
     by the wrapper object's identity rather than the action's. Fixed by
     deduping on `id(action)` instead of `id(p)`. Regression test added in
     both `test_jobs.py`'s `TestListUiActions` and the real-plugin
     integration test from (1).
- Full `pytest` run after all three fixes: 1461 passed, the same one
  pre-existing unrelated failure as before this work started.

Reasoning that came out of your answers to three open questions, recorded
here since it shaped the shape of what got built:

- **Whole-file export, not page-by-page** — turned out to need *no* new
  assembly logic: `translate_document()` already builds the whole
  translated document in memory and writes it once via
  `file_output.save_translation_output()`. `on_progress` only had to
  become a lightweight status ping, not a place to hang a per-page
  download.
- **In-memory jobs, no resume** — a webui restart mid-job loses that
  job's thread and tracking entry together; `sweep_stale_jobs()` is what
  keeps a conversation from being left locked forever because of that,
  not an attempt to actually resume. The recovery story is manual: rerun
  with the page-range field set to wherever it stopped, and combine the
  output files yourself — which is why page range moved into `translate`'s
  v1 field set instead of staying deferred.
- **Two-pane options/live-prompt-preview panel** — built in the front-end
  pass above, exactly as designed: reusing each service's existing
  `build_prompts()` method (the same one `--dry-run` already calls) behind
  a new, cheap `preview_ui_action`/`POST .../preview` endpoint, called on
  every field change.

The rest of this section is unchanged design narrative from before either
pass — including why the generic attachment icon came out in the first
place and the v1 field scope reasoning — kept as-is since it's still the
accurate design, and is now also the accurate *implementation*.

### The real thing: plugins declare composer actions, generically

Rather than the webui hard-coding "there's a Translate button and a
Transcribe button," any plugin can opt in to appearing in the composer by
declaring an optional `ui_action` attribute — same spirit as the existing
optional `requires_professor` attribute in `src/runtime/plugin.py` (§5):
absent by default, so no existing plugin needs to change, and any plugin
installed later (not just these two) picks up a composer entry for free
just by declaring one.

```python
# src/runtime/ui_action.py (new)
@dataclass
class UiField:
    name: str                 # "source_language"
    label: str                 # "Source language"
    kind: str                  # "language" | "file" | "checkbox" | "text"
    required: bool = True

@dataclass
class UiAction:
    id: str                    # "translate"
    label: str                  # "Translate a document"
    command: str                 # "translate" — routes to plugins[command].run()
    fields: list[UiField]
```

`plugins/translation/plugin.py` and `plugins/transcription/plugin.py` each
add a module-level `ui_action` built from their own `handles`/language
registry — the webui never hard-codes language lists or per-plugin
specifics, it just renders whatever field `kind`s come back.

**v1 field scope (your call: "core subset, but the intent is to allow it
all")** — deliberately mirrors only part of each command's real flag set,
chosen as the smallest useful slice:
- `translate`: source language, target language, file (or folder later),
  a `scanned` checkbox, a free-text notes field.
- `transcribe`: target language, file/folder, notes.
- `transcription_review`: left out of v1 — it consumes the *output* of a
  prior transcription as pasted/uploaded text, which is a less natural
  composer action than "process this document."

Every other flag this plan's §1–§9 material already covers for the CLI
(`--preserve-media`, `--toc`, `--preserve-tables`, `--spread`, `--abstract`,
page ranges, parallel `workers`, `--dry-run`) is a later addition to the
same `UiField` list — additive, not a redesign, because the form is
data-driven off whatever fields a plugin declares.

### The composer button opens a two-pane action panel, not a small popover

Where the paperclip used to be: a puzzle-piece icon. Clicking it calls
`GET /api/plugin-actions` (walks the already-loaded plugin registry the
same way `cli.py` does, collecting every plugin's declared `ui_action`)
and shows a short list — "Translate a document," "Transcribe an image,"
etc. Picking one opens a large modal (bigger than the settings modal —
around 1100px/95vw), split like this:

```
┌───────────────┬─────────────────────────────────────┐
│  Options       │  System prompt                       │
│  (narrower,    │  (large, its own scrollbar)           │
│  left column)  │                                       │
│                ├─────────────────────────────────────┤
│  [Run job]     │  User prompt                         │
│                │  (large, its own scrollbar)           │
└───────────────┴─────────────────────────────────────┘
```

This is your call, and it maps onto something that already exists rather
than being invented from scratch: `--dry-run` doesn't print a prompt
preview from nothing — it calls the same `build_prompts()` method on the
plugin's own service (e.g. `sandbox.translation_service.build_prompts(
source_language, target_language)`) that a real run uses, then prints the
two returned strings instead of sending them anywhere. That call makes no
API request and costs nothing, which is exactly what makes "re-run it on
every keystroke" viable. A new endpoint,
`POST /api/plugin-actions/{id}/preview`, takes the form's current field
values (professor, languages, `scanned`, the notes field, and — once a
file's been uploaded — its extracted first-page text) and returns
`{"system_prompt": ..., "user_prompt": ...}`. The front end calls it on
every field change, debounced (~300ms) for the notes textarea so typing
doesn't fire a request per keystroke, and re-renders both panes. Before a
file is uploaded, the preview uses the same placeholder text `--dry-run`
already shows with no `-i` (e.g. `"[Japanese document text]"`); after
upload, it swaps in the real extracted text, same as `-i` does today.
Notes map onto the existing `-nb` ("note both") inline-note mechanism
(`_apply_inline_notes` in `src/runtime/command_runner.py`) — v1 is one
combined notes field; separate system/user notes (`-ns`/`-nu`) are a
later addition to the same field list, not a redesign.

**v1 field scope, revised** — page range (`-p`/`--page_nums`, already a
real CLI flag, `validate_page_nums`) is now in the core set, not deferred,
because it's the answer to the restart question below: `translate`
source/target language, file, `scanned` checkbox, page range, notes.
`transcribe`: target language, file, notes (no page range — an image
folder is indexed by filename order, not page numbers).

### Execution: an in-memory background job tied to the conversation it was started from

Not streamed synchronously (too heavy for a multi-minute, many-page job),
and not a separate "check back later" page — the job runs in the
background, and lightweight progress pings ("page 3 of 12…") land in the
*same conversation* while it runs, so there's visible proof of life. The
actual deliverable is **one file, produced once, at the end** — your call,
and it turns out to need no new assembly logic:
`translate_document()`/`translation_service` already builds the whole
translated document in memory and writes it in one shot via
`file_output.save_translation_output()` — nothing about that changes. What
was missing is only a way for a caller to *observe* progress as it happens
rather than just reading print/log output:

- `plugins/webui/src/jobs.py` (new): an **in-memory** `dict[str, Job]`
  (job id → status/professor/conversation id/plugin command), your call —
  process-lifetime only, the same tradeoff this plugin already makes for
  the session-cookie secret (§3's `create_app()`). Each job runs on a
  background thread (same reasoning as the existing chat-streaming
  generator in `app.py`: the real work is blocking network calls, so a
  thread is the right tool, not `asyncio`).
- `translate_document()` gains an optional
  `on_progress: Callable[[int, int], None] | None = None` parameter
  (page index, total pages) threaded through to
  `_process_text_based_file()` → `translation_service.translate_text_pages()`
  (and the PDF branch, and `process_image_translation_folder()`'s
  per-image loop) — additive and optional everywhere, so the CLI path
  (`_execute_translate()`, which never passes it) is unaffected. The
  webui's job runner is the only caller that ever passes one; it turns
  each finished page into a status ping, not a downloadable artifact.
- `Conversation` gains `active_job_id: Optional[str]`. While set,
  `POST /api/chat` on that conversation is rejected (409) and the
  composer's textarea is disabled with placeholder text along the lines
  of *"A translate job is running in this conversation — start a new
  conversation if you'd like to keep chatting while it finishes."*
  (still needs a copy pass, not load-bearing for the design). The front
  end polls `GET /api/conversations/{id}` every couple seconds while
  `active_job_id` is set and appends whatever's new — a progress ping
  message, or the final one carrying the finished file
  (downloadable, same `FileResponse` pattern
  `/api/conversations/{id}/export` already uses) — then clears the lock.

### Restart behavior and interrupted jobs: your call — in-memory, no resume

If the webui process itself restarts mid-job (crash, manual restart, a
reboot), the job dict and its thread are simply gone — nothing resumes
automatically. Two consequences this design accepts on purpose, per your
answer:

1. **Startup sweep, not silent amnesia.** Because `active_job_id` lives on
   the conversation (on disk), a restarted server has to explicitly clear
   any conversation left pointing at a job id that no longer exists in the
   fresh, empty in-memory job dict — otherwise that conversation's
   composer would stay locked forever with no job actually running to
   unlock it. `create_app()` does this once at startup: any
   `active_job_id` found on disk is cleared, and a short system-style
   message ("Translate job did not finish — it was interrupted.") is
   appended so the interruption is visible in the transcript, not just
   silently undone.
2. **No resume — the escape valve is the page-range field, not new
   machinery.** Rather than building checkpointed state so a job could
   pick back up from page 8, the person just re-opens the same action
   panel, sets the page-range field to `8-12` (the same `-p`/`--page_nums`
   flag the CLI already validates), runs it as a fresh job, and manually
   combines the two output files themselves. This is why page range moved
   into the v1 core field set above — it was a "later" flag until it
   became the actual interrupted-job story, at which point it's load-bearing.

### Open items before this is buildable end-to-end
- Exact composer lock/placeholder copy (still a copy pass, not a design gap).
- Folder input (a whole directory of images for `transcribe`) needs a
  different file widget than the single-file `<input type=file>` — deferred
  past v1's "one file" scope, same as before.
