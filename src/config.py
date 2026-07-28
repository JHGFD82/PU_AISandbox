"""Helpers for language registration, professor configuration, and API key lookup.

This module is the starting point for two things plugin developers commonly need:
registering the languages their plugin supports (via ``register_language``) and
looking up a professor's API credentials at runtime (via ``get_api_key``).
Professor configuration and model pricing are kept separate — pricing lives in
``src/models/``.
"""

import argparse
import re
from dataclasses import dataclass

from .errors import CLIError

# Language registry — starts empty; populated by plugins at import time via
# register_language().  The framework needs this dict and the parser functions
# here (argparse type= hooks must be importable from src), but the *entries*
# are entirely the responsibility of each language plugin.
LANGUAGE_MAP: dict[str, str] = {}

# Tracks which codes were registered by plugins.
_PLUGIN_LANGUAGES: set[str] = set()


@dataclass(frozen=True)
class SettingField:
    """One optional ``settings.toml`` value that a plugin or core feature knows how to use.

    Lets ``--show-config`` and the ``settings`` command list every optional
    value in the project without hard-coding plugin-specific knowledge —
    each plugin declares its own by calling ``register_setting()`` once at
    import time, the same way it calls ``register_language()`` to add a
    language.

    Attributes:
        key: The dotted path the value lives at in ``settings.toml``
             (e.g. ``'webui.session_secret'``).
        label: A short, plain-English description shown next to it in
               ``--show-config`` (e.g. ``'Session signing secret'``).
        section: A group heading used to cluster related values when they're
                 displayed (e.g. ``'Web UI plugin'``).
        secret: Whether the value is sensitive. When ``True``, only whether
                it is set is ever shown — never the value itself.
    """

    key: str
    label: str
    section: str = "Other"
    secret: bool = False


# Populated by plugins (and core) at import time via register_setting().
_SETTING_FIELDS: dict[str, SettingField] = {}


def register_setting(key: str, label: str, *, section: str = "Other", secret: bool = False) -> None:
    """Declare an optional ``settings.toml`` value so it shows up in ``--show-config`` and ``settings list``.

    Call this once, at import time, for every optional value a plugin reads
    — mirroring how ``register_language()`` works for languages. Without it,
    someone running ``--show-config`` would have no way to discover an
    optional setting exists short of reading that plugin's source code.

    Args:
        key: The dotted path the value lives at in ``settings.toml``
             (e.g. ``'webui.session_secret'``).
        label: A short, plain-English description of what the setting is for.
        section: A group heading for display purposes (e.g.
                 ``'Web UI plugin'``). Defaults to ``'Other'``.
        secret: Set to ``True`` if the value is sensitive — an API key, a
                password, a signing secret — so its value is never printed
                or echoed back, only whether it's currently set. Defaults to
                ``False``.
    """
    _SETTING_FIELDS[key] = SettingField(key=key, label=label, section=section, secret=secret)


def get_registered_settings() -> list[SettingField]:
    """Return every optional ``settings.toml`` value registered so far, grouped by section for display."""
    return sorted(_SETTING_FIELDS.values(), key=lambda f: (f.section, f.key))


def register_language(code: str, name: str) -> None:
    """Make a new language available for use in translate and transcribe commands.

    Plugins call this once at startup to declare which languages they support.
    After registration, the short code (e.g. ``'jp'``) can be typed on the
    command line and will be resolved to its full name (e.g. ``'Japanese'``)
    before being sent to the AI service.

    Args:
        code: The short code the user types on the command line (e.g. ``'jp'``,
              ``'zh'``, ``'en'``). Stored in lowercase regardless of how it is
              passed in.
        name: The full language name forwarded to the AI service
              (e.g. ``'Japanese'``, ``'Chinese'``, ``'English'``).
    """
    code = code.lower()
    LANGUAGE_MAP[code] = name
    _PLUGIN_LANGUAGES.add(code)


# A netID is letters and digits, nothing else. Princeton issues them in no
# single fixed shape — most are a couple of letters followed by a couple of
# digits, and most are eight characters or fewer, but neither is guaranteed,
# so neither is enforced here. What *is* enforced is the part the sandbox
# depends on: a netID contains nothing that means something special to a
# filesystem, so it can be used as a file or folder name exactly as typed.
_NETID_RE = re.compile(r"^[a-z0-9]+$")


def normalize_netid(value: str) -> str:
    """Check that something looks like a netID and return it in its standard form.

    A netID is the university username a person signs in with — ``jh43``,
    for example. The sandbox uses it as the one name for a person: it picks
    the right API key, names their usage file, and labels their spending in
    reports.

    Capital letters are accepted and folded to lower case, so ``JH43`` and
    ``jh43`` are the same person rather than two people with separate
    running totals. Surrounding spaces are ignored, since they are almost
    always a copy-and-paste artefact.

    Args:
        value: The netID as typed, on the command line or in ``settings.toml``.

    Returns:
        The netID in lower case, safe to use directly as a file or folder
        name.

    Raises:
        CLIError: If it is empty or contains anything other than letters and
                  digits — a space, a dot, a slash. The message says what was
                  wrong, because the usual cause is a display name being
                  entered where a netID was wanted.
    """
    candidate = value.strip().lower()
    if not candidate:
        raise CLIError(
            "A netID is required, but nothing was given. A netID is the "
            "university username someone signs in with, such as 'jh43'."
        )
    if not _NETID_RE.match(candidate):
        raise CLIError(
            f"'{value}' doesn't look like a netID. A netID is the university "
            "username someone signs in with — letters and digits only, such "
            "as 'jh43'. If you meant to give a person's full name, that goes "
            "in the 'name' field instead; the netID is what identifies them."
        )
    return candidate


def _language_keys_str() -> str:
    """Return a human-readable list of valid language keys derived from LANGUAGE_MAP."""
    return ', '.join(sorted(LANGUAGE_MAP.keys()))


def parse_single_language_code(value: str) -> str:
    """Validate a language code typed on the command line and return its full name.

    Used by the argument parser for transcribe and OCR commands, which accept a
    single target language. If the code is not recognised, an error message is
    shown to the user directly in the terminal.

    Args:
        value: The short language code typed by the user (e.g. ``'jp'``,
               ``'zh'``, ``'en'``).

    Returns:
        The full language name for that code (e.g. ``'Japanese'``, ``'Chinese'``,
        ``'English'``), ready to be passed to the AI service.

    Raises:
        argparse.ArgumentTypeError: If the code is not in the list of languages
            registered by the installed plugins. The error message lists all
            valid codes.
    """
    code = value.strip().lower()
    if code not in LANGUAGE_MAP:
        raise argparse.ArgumentTypeError(
            f"Invalid language code '{value}'. Use one of: {_language_keys_str()}."
        )
    return LANGUAGE_MAP[code]


def parse_language_code(value: str) -> str | tuple[str, str]:
    """Validate a language argument typed on the command line and return it in a form the service layer can use.

    Accepts two formats depending on the command:

    - **Single code** (``'en'``, ``'jp'``) — used for OCR and transcription,
      where only a target language is needed. Returns the full language name
      (e.g. ``'English'``).
    - **Hyphen-separated pair** (``'jp-en'``, ``'zh-en'``) — used for
      translation, where a source and target language are both required. Returns
      a two-item tuple of short codes (e.g. ``('jp', 'en')``). Each plugin's
      ``run()`` method resolves these codes to full names before passing them to
      the service layer.

    Args:
        value: The language argument as typed on the command line
               (e.g. ``'jp'`` or ``'jp-en'``).

    Returns:
        Either a full language name string (single-code path) or a
        ``(source_code, target_code)`` tuple (pair path).

    Raises:
        argparse.ArgumentTypeError: If any code in the value is not registered,
            if a pair contains more than two parts, or if source and target are
            the same language.
    """
    valid_keys = _language_keys_str()

    # Normalise: strip whitespace, lower-case
    value = value.strip().lower()

    # Hyphen-separated pair — e.g. "jp-en"
    if '-' in value:
        parts = value.split('-')
        if len(parts) != 2:
            raise argparse.ArgumentTypeError(
                f"Invalid language pair '{value}'. Use source-target format, e.g. jp-en or zh-en."
            )
        source_code, target_code = parts
        if source_code not in LANGUAGE_MAP:
            raise argparse.ArgumentTypeError(f"Invalid source language '{source_code}'. Use one of: {valid_keys}.")
        if target_code not in LANGUAGE_MAP:
            raise argparse.ArgumentTypeError(f"Invalid target language '{target_code}'. Use one of: {valid_keys}.")
        if source_code == target_code:
            raise argparse.ArgumentTypeError("Source and target languages cannot be the same.")
        return source_code, target_code

    # Single code → OCR path (returns full name)
    if value not in LANGUAGE_MAP:
        raise argparse.ArgumentTypeError(f"Invalid language code '{value}'. Use one of: {valid_keys}.")
    return LANGUAGE_MAP[value]



def load_professor_config() -> dict[str, dict[str, str]]:
    """Read everyone configured on this installation and return them as a lookup table.

    Reads from ``settings.toml`` (see ``src/settings_store.py``). The lookup
    table is keyed by netID — the university username, e.g. ``'jh43'`` —
    which is also what gets typed on the command line, so a typed name can
    be looked up here directly.

    Returns:
        A dictionary where each key is a netID and each value is a
        sub-dictionary with the keys ``'name'`` (display name, for showing
        to people), ``'key'`` (primary API key value), ``'backup_key'``
        (backup API key value, or ``None``), and ``'netid'`` (same as the
        outer key). Returns an empty dictionary if nobody is configured.
    """
    from . import (
        settings_store,  # deferred: settings_store imports normalize_netid from here
    )
    return settings_store.get_professors()


def get_api_key(netid: str) -> tuple[str, str]:
    """Look up someone's API key (the private credential that grants access to the AI service).

    Tries their primary key first, then falls back to the backup key if the
    primary is not set.

    Looked up by netID only. A display name is *not* accepted, even though
    it once was: two ways of naming the same person is how one person's
    spending ended up recorded under two different names, and a netID is
    already the one identifier the university guarantees is unique.

    Args:
        netid: The netID as typed on the command line (e.g. ``'jh43'``).
               Capitalisation doesn't matter.

    Returns:
        A two-item tuple of ``(api_key, display_name)`` where ``api_key`` is
        the credential string read from ``settings.toml`` and ``display_name`` is
        the person's full name as configured (e.g. ``'Jeff Heller'``), for
        showing in messages.

    Raises:
        ValueError: If the netID is not configured, or if neither their
                    primary nor backup API key is set.
        CLIError: If *netid* isn't shaped like a netID at all.
    """
    netid = normalize_netid(netid)
    professors = load_professor_config()

    prof_config = professors.get(netid)
    if prof_config is None:
        if professors:
            known = ", ".join(
                f"{n} ({c['name']})" for n, c in sorted(professors.items())
            )
            error_msg = (
                f"No one with the netID '{netid}' is configured.\n"
                f"Configured netIDs: {known}\n\n"
                f"To add someone: python main.py env add-professor"
            )
        else:
            error_msg = (
                "No one is configured yet.\n"
                "Add someone with: python main.py env add-professor\n"
                "(or by hand — see templates/settings.template for the format)"
            )
        raise ValueError(error_msg)

    primary_key = prof_config.get('key')
    if primary_key:
        return primary_key, prof_config['name']

    backup_key = prof_config.get('backup_key')
    if backup_key:
        print(f"Warning: Using backup API key for {prof_config['name']}")
        return backup_key, prof_config['name']

    raise ValueError(
        f"No API key found for professor '{prof_config['name']}'. "
        f"Please set one with: python main.py env add-professor"
    )

