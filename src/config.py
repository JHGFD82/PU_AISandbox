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

# Language registry — starts empty; populated by plugins at import time via
# register_language().  The framework needs this dict and the parser functions
# here (argparse type= hooks must be importable from src), but the *entries*
# are entirely the responsibility of each language plugin.
LANGUAGE_MAP: dict[str, str] = {}

# Tracks which codes were registered by plugins.
_PLUGIN_LANGUAGES: set[str] = set()


@dataclass(frozen=True)
class EnvField:
    """One optional ``.env`` variable that a plugin or core feature knows how to use.

    Lets ``--show-config`` and the ``env`` command list every optional
    setting in the project without hard-coding plugin-specific knowledge —
    each plugin declares its own fields by calling ``register_env_field()``
    once at import time, the same way it calls ``register_language()`` to
    add a language.

    Attributes:
        key: The exact environment-variable name, matching what appears in
             ``.env`` (e.g. ``'WEBUI_SESSION_SECRET'``).
        label: A short, plain-English description shown next to it in
               ``--show-config`` (e.g. ``'Session signing secret'``).
        section: A group heading used to cluster related fields when they're
                 displayed (e.g. ``'Web UI plugin'``).
        secret: Whether the value is sensitive. When ``True``, only whether
                it is set is ever shown — never the value itself.
    """

    key: str
    label: str
    section: str = "Other"
    secret: bool = False


# Populated by plugins (and core) at import time via register_env_field().
_ENV_FIELDS: dict[str, EnvField] = {}


def register_env_field(key: str, label: str, *, section: str = "Other", secret: bool = False) -> None:
    """Declare an optional ``.env`` variable so it shows up in ``--show-config`` and ``env`` commands.

    Call this once, at import time, for every optional environment variable
    a plugin reads — mirroring how ``register_language()`` works for
    languages. Without this, a person running ``--show-config`` would have
    no way to discover an optional setting exists short of reading that
    plugin's source code.

    Args:
        key: The exact environment-variable name (e.g.
             ``'WEBUI_SESSION_SECRET'``).
        label: A short, plain-English description of what the setting is for.
        section: A group heading for display purposes (e.g.
                 ``'Web UI plugin'``). Defaults to ``'Other'``.
        secret: Set to ``True`` if the value is sensitive — an API key, a
                password, a signing secret — so its value is never printed
                or echoed back, only whether it's currently set. Defaults to
                ``False``.
    """
    _ENV_FIELDS[key] = EnvField(key=key, label=label, section=section, secret=secret)


def get_registered_env_fields() -> list[EnvField]:
    """Return every optional ``.env`` field registered so far, grouped by section for display."""
    return sorted(_ENV_FIELDS.values(), key=lambda f: (f.section, f.key))


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


def make_safe_filename(name: str) -> str:
    """Convert a name to a version that is safe to use inside a file or folder name.

    Spaces and most special characters are replaced with underscores, consecutive
    underscores are collapsed to one, and the result is lowercased. This produces
    a consistent, filesystem-friendly identifier (safe filename) used throughout
    the project to name token-usage files and archive folders.

    For example: ``'Jeff Heller'`` → ``'jeff_heller'``,
    ``'Smith & Jones'`` → ``'smith_jones'``.

    Args:
        name: Any string — typically the professor's display name as set in
              ``.env`` (e.g. ``'Jeff Heller'``).

    Returns:
        A lowercase string with only letters, digits, hyphens, and underscores
        (e.g. ``'jeff_heller'``).
    """
    safe_name = re.sub(r'[^\w\-_\.]', '_', name)
    safe_name = re.sub(r'_+', '_', safe_name)
    safe_name = safe_name.strip('_')
    return safe_name.lower()


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
    """Read all professor configurations and return them as a lookup table.

    Reads from ``.settings`` (see ``src/settings_store.py``) rather than
    environment variables. The lookup table is keyed by the safe-filename
    form of the professor's name (e.g. ``'heller'``) so it can be matched
    against command-line input.

    Returns:
        A dictionary where each key is the professor's safe-filename
        identifier (e.g. ``'heller'``) and each value is a sub-dictionary
        with the keys ``'name'`` (display name), ``'key'`` (primary API key
        value), ``'backup_key'`` (backup API key value, or ``None``), and
        ``'safe_name'`` (same as the outer key). Returns an empty dictionary
        if no professors are configured.
    """
    from . import (
        settings_store,  # deferred: settings_store imports make_safe_filename from here
    )
    return settings_store.get_professors()


def get_api_key(professor_name: str) -> tuple[str, str]:
    """Look up a professor's API key (the private credential that grants access to the AI service).

    Tries the professor's primary key first, then falls back to the backup key
    if the primary is not set. Accepts both the professor's display name
    (e.g. ``'Heller'``) and the safe-filename form (e.g. ``'heller'``) so
    either can be typed on the command line.

    Args:
        professor_name: The professor's name or safe-filename identifier as
                        typed on the command line (e.g. ``'heller'``).

    Returns:
        A two-item tuple of ``(api_key, display_name)`` where ``api_key`` is
        the credential string read from ``.settings`` and ``display_name`` is
        the professor's full name as configured (e.g. ``'Jeff Heller'``).

    Raises:
        ValueError: If the professor name is not found in the configuration, or
                    if neither their primary nor backup API key is set.
    """
    professors = load_professor_config()

    if professor_name in professors:
        prof_config = professors[professor_name]
    else:
        prof_config = None
        for _, config in professors.items():
            if config['name'].lower() == professor_name.lower():
                prof_config = config
                break

        if prof_config is None:
            available_names = [config['name'] for config in professors.values()]
            available_safe = list(professors.keys())

            if available_names:
                error_msg = (
                    f"Professor '{professor_name}' not found. Available professors:\n"
                    f"Full names: {', '.join(available_names)}\n"
                    f"CLI names: {', '.join(available_safe)}\n\n"
                    f"To add a new professor: python main.py env add-professor"
                )
            else:
                error_msg = (
                    "No professors configured yet.\n"
                    "Add one with: python main.py env add-professor\n"
                    "(or by hand — see .settings.template for the format)"
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

