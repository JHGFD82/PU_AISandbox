"""Load user-customizable settings from settings.default.toml at the repository root.

All tuneable runtime defaults live in that file. This module reads it once at
import time and exposes each value as a typed module-level constant so other
modules can import them directly.

Settings are merged from up to three layers, in this order (later layers
override earlier ones, key by key — a layer only needs to mention the
settings it wants to change):

1. ``settings.default.toml`` — the repo's checked-in defaults, same for everyone.
2. A shared file, if ``shared_settings.path`` is set in ``settings.toml``
   (see ``src/settings_store.py``) — e.g. one synced with Dropbox between
   everyone in a group. Lets a group share defaults — a shared cluster's
   worker count, a group-wide font size, even a shared alternate-endpoint
   definition (see the ``[endpoints]`` section below) — without anyone
   hand-editing their own copy. Absent by default; nothing changes unless
   this is set. A common case: one person manages several professors'
   installations and wants them all to use the same tuned settings.
3. ``preferences.toml`` in this person's own files folder — their own
   adjustments, and the last word. Even with a shared file in play, a
   setting placed here wins, so one person can override just their own
   quirk without touching the file everyone else reads.

The ``[endpoints]`` section (alternate AI API endpoint *definitions* — base
URL, timeout, etc.) merges through these same three layers exactly like
every other section. Only the credential for each endpoint lives outside
this file, in ``settings.toml`` (since credentials are never meant to be shared
or layered) — see ``src/services/api_config.py`` for how the two combine.
"""

import logging
import sys
from pathlib import Path

import tomllib

from . import paths, settings_store
from .config import register_setting

_ROOT = Path(__file__).parent.parent  # src/ -> repo root
_TOML_PATH = _ROOT / "settings.default.toml"
# Resolved defensively: a copy of the sandbox that hasn't been set up yet
# has no preferences file, and asking where one would be is itself an error
# at that point. No preferences simply means the defaults below stand.
try:
    _PREFERENCES_PATH = paths.preferences_path()
except paths.NotSetUpError:
    _PREFERENCES_PATH = None

register_setting(
    "shared_settings.path",
    "Path to a shared settings file, merged between the defaults and your preferences",
    section="Core",
    secret=False,
)


def _merge_layer(base: dict, layer_path: Path) -> None:
    """Merge one TOML file's sections into *base* in place.

    Only the keys present in *layer_path* are overridden — a layer that
    doesn't mention a section, or a key within one, leaves whatever's
    already in *base* for that key untouched.
    """
    with layer_path.open("rb") as _f:
        layer = tomllib.load(_f)
    for section, values in layer.items():
        if section in base and isinstance(base[section], dict) and isinstance(values, dict):
            base[section].update(values)
        else:
            base[section] = values


try:
    with _TOML_PATH.open("rb") as _f:
        _s = tomllib.load(_f)
except FileNotFoundError:
    # `from None` because the message below says everything worth saying; the
    # original "no such file" would only add noise above it.
    raise FileNotFoundError(
        f"settings.default.toml not found at {_TOML_PATH}. "
        "Copy settings.default.toml from the repository root and edit it to configure the sandbox."
    ) from None

# Layer 2: shared settings, only if settings.toml points at a real file.
_shared_settings_path = settings_store.get_shared_settings_path()
if _shared_settings_path:
    if _shared_settings_path.exists():
        _merge_layer(_s, _shared_settings_path)
    else:
        import logging
        logging.getLogger(__name__).warning(
            f"shared_settings.path is set to '{_shared_settings_path}', but no file exists "
            "there — shared settings were not applied."
        )

# Layer 3: preferences.toml — this person's own adjustments, still the last word.
if _PREFERENCES_PATH is not None and _PREFERENCES_PATH.exists():
    _merge_layer(_s, _PREFERENCES_PATH)

def plugin_settings(caller_file: str, *sections: str) -> dict:
    """Return one plugin's settings, with this person's own adjustments applied.

    A plugin ships its defaults in its own ``settings.toml``, next to its code.
    Those are the starting point, not the last word: the same two layers that
    override the sandbox's own defaults override a plugin's as well, so a
    worker count or a font size can be adjusted per group or per person without
    editing a file that belongs to the plugin's repository.

    The plugin names its own sections and passes its own location; this handles
    the layering, because where those layers live is the package's business and
    not something each plugin should have to work out for itself.

    Args:
        caller_file: The calling module's ``__file__``. Used to find the
                     plugin's own ``settings.toml`` by walking up from there,
                     so a plugin doesn't have to know its own path.
        *sections: The TOML section names this plugin owns (e.g.
                   ``'translation'``, ``'image_translation'``). Only these are
                   returned, so one plugin can't read another's settings by
                   accident.

    Returns:
        One dictionary per named section, each holding the plugin's own values
        with any overrides applied on top. A section nobody has configured
        comes back as an empty dictionary rather than being absent, so callers
        can use ``.get(key, default)`` without checking first.

    Notes:
        Order is the same as everywhere else in the sandbox: the plugin's own
        file, then a shared settings file if one is configured, then
        ``preferences.toml``, which wins.
    """
    merged: dict = {section: {} for section in sections}

    # The plugin's own file: the nearest settings.toml above it that actually
    # mentions one of its sections. Checking the contents rather than stopping
    # at the first file found matters — a plugin nested in another directory
    # that happens to hold a settings.toml would otherwise read the wrong one.
    directory = Path(caller_file).resolve().parent
    while directory != directory.parent:
        candidate = directory / "settings.toml"
        if candidate.exists():
            try:
                with candidate.open("rb") as handle:
                    own = tomllib.load(handle)
            except (OSError, tomllib.TOMLDecodeError):
                own = {}
            if any(section in own for section in sections):
                for section in sections:
                    if isinstance(own.get(section), dict):
                        merged[section].update(own[section])
                break
        directory = directory.parent

    for layer in (_shared_settings_path, _PREFERENCES_PATH):
        if layer is None or not layer.exists():
            continue
        try:
            with layer.open("rb") as handle:
                data = tomllib.load(handle)
        except (OSError, tomllib.TOMLDecodeError):
            # A layer that can't be read leaves the ones below it standing,
            # which is the same thing the sandbox's own settings do.
            continue
        for section in sections:
            if isinstance(data.get(section), dict):
                merged[section].update(data[section])

    return merged


# ── Custom prompt ──────────────────────────────────────────────────────────────
DEFAULT_SYSTEM_PROMPT: str = _s["prompt"]["default_system_prompt"]
PROMPT_TEMPERATURE: float = _s["prompt"]["temperature"]
PROMPT_TOP_P: float = _s["prompt"]["top_p"]
PROMPT_MAX_TOKENS: int = _s["prompt"]["max_tokens"]

# ── Retry / rate limiting ──────────────────────────────────────────────────────
PAGE_DELAY_SECONDS: float = _s["retry"]["page_delay_seconds"]
MAX_RETRIES: int = _s["retry"]["max_retries"]

# How long to wait between retries, in seconds. The same every time.
RETRY_DELAY_SECONDS: float = _s["retry"]["retry_delay_seconds"]

# ── Parallelism & document processing ─────────────────────────────────────────
DEFAULT_PARALLEL_WORKERS: int = _s["processing"]["default_parallel_workers"]
DEFAULT_PAGE_SIZE: int = _s["processing"]["default_page_size"]
MAX_PARALLEL_WORKERS: int = _s["processing"]["max_parallel_workers"]

# ── Transcription (plugin) ────────────────────────────────────────────────────
# DEFAULT_OCR_PASSES and TRANSCRIPTION_REVIEW_* constants are provided by the
# transcription plugin. Access via src.settings.__getattr__ below, which
# searches pu_plugin.*.settings.

# ── Output formatting ─────────────────────────────────────────────────────────
DEFAULT_FONT_SIZE: int = _s["output"]["default_font_size"]

# ── Budget ─────────────────────────────────────────────────────────────────────
BUDGET_WARNING_THRESHOLD: int = _s["budget"]["warning_threshold_pct"]

# ── Alternate AI API endpoints (definitions only — credentials live in settings.toml) ──
ENDPOINTS: dict = _s.get("endpoints", {})
DEFAULT_ENDPOINT = _s.get("config", {}).get("default_endpoint") or None


def __getattr__(name: str):
    """Delegate plugin-specific settings lookups to registered plugin settings modules.

    Any plugin that registers a module as ``pu_plugin.<name>.settings`` via
    ``_register()`` will have its constants available via ``from src.settings
    import <CONSTANT>`` without any changes to this file.

    If two plugins define the same constant, the first one found wins and a
    warning names both, since which one that is depends on load order rather
    than on anything meaningful — a silent coin-toss over a configuration
    value is worth knowing about.
    """
    # Iterating over a *copy* of the module registry matters here. Python
    # adds an entry to it every time anything is imported anywhere, and the
    # web interface imports on demand from inside request handlers running on
    # several threads at once. Iterating the live registry while another
    # thread imports something raises "dictionary changed size during
    # iteration" and fails a request for reasons having nothing to do with
    # that request.
    matches = [
        (mod_name, mod)
        for mod_name, mod in list(sys.modules.items())
        if (
            mod_name.startswith("pu_plugin.")
            and mod_name.endswith(".settings")
            and mod is not None
            and hasattr(mod, name)
        )
    ]
    if not matches:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    if len(matches) > 1:
        logging.warning(
            "The setting %r is defined by more than one plugin (%s). Using the one from "
            "%r. Rename it in all but one of them — which plugin wins here depends on "
            "the order they happened to load in, so this may behave differently on "
            "another machine.",
            name,
            ", ".join(mod_name for mod_name, _ in matches),
            matches[0][0],
        )
    return getattr(matches[0][1], name)
