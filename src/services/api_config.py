"""Configuration for AI API endpoints.

Each API is declared as a ``[apis.<name>]`` section in ``settings.toml``.
The section name (e.g. ``pu_sandbox``) becomes the identifier used on the
CLI (``--api pu_sandbox``) and in the colon syntax (``-m della:qwen``).

A default API can be set in ``settings.toml`` under ``[apis]``::

    [apis]
    default = "pu_sandbox"

When a default is set, bare model names (without a colon prefix) are routed
to that API instead of the built-in Portkey/Sandbox service.

The corresponding API key is read from the environment variable
``EXTERNAL_API_<UPPERCASE_NAME>_KEY`` (e.g. ``EXTERNAL_API_PU_SANDBOX_KEY``).

Example settings.toml::

    [apis.pu_sandbox]
    name = "PU AI Sandbox"
    base_url = "https://api.aisandbox.princeton.edu/v1"
    openai_compatible = true
    default_model = "gpt-4o"
    timeout = 30
    verify_ssl = true

Example .env::

    EXTERNAL_API_PU_SANDBOX_KEY=your_key_here

Colon syntax on the CLI::

    python main.py heller prompt -m pu_sandbox:gpt-4o-mini
    python main.py heller prompt -m della:qwen-preview
    python main.py heller prompt -m gpt-4o          # uses apis.default if set
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).parent.parent.parent  # src/services/ -> repo root
_TOML_PATH = _ROOT / "settings.toml"
_LOCAL_TOML_PATH = _ROOT / "settings.local.toml"


def _load_raw_settings() -> dict:
    """Load and merge settings.toml + settings.local.toml."""
    try:
        with _TOML_PATH.open("rb") as f:
            settings = tomllib.load(f)
    except FileNotFoundError:
        return {}

    if _LOCAL_TOML_PATH.exists():
        with _LOCAL_TOML_PATH.open("rb") as f:
            local = tomllib.load(f)
        for section, values in local.items():
            if section in settings and isinstance(settings[section], dict):
                settings[section].update(values)
            else:
                settings[section] = values

    return settings


@dataclass
class APIConfig:
    """Configuration for a single AI API endpoint.

    Attributes:
        api_name:           The section key used in settings.toml (e.g. ``pu_sandbox``).
        display_name:       Human-readable name shown in logs and --list-apis output.
        base_url:           The root URL for the API (e.g. ``https://api.example.com/v1``).
        api_key:            Resolved API key (from environment variable).
        openai_compatible:  When True, use the OpenAI SDK with ``base_url`` for LLM calls.
                            When False, use ``requests`` for generic HTTP calls.
        default_model:      Default model name for OpenAI-compatible endpoints.
        timeout:            Request timeout in seconds.
        verify_ssl:         Whether to verify SSL certificates.
    """

    api_name: str
    display_name: str
    base_url: str
    api_key: str
    openai_compatible: bool = False
    default_model: Optional[str] = None
    timeout: int = 30
    verify_ssl: bool = True
    extra: dict = field(default_factory=dict)


def _env_key_for(api_name: str) -> str:
    """Return the env-var name for the given API section name.

    Examples::

        _env_key_for("pu_sandbox")  -> "EXTERNAL_API_PU_SANDBOX_KEY"
        _env_key_for("my-cluster")  -> "EXTERNAL_API_MY_CLUSTER_KEY"
    """
    safe = api_name.upper().replace("-", "_")
    return f"EXTERNAL_API_{safe}_KEY"


def load_api_config(api_name: str) -> APIConfig:
    """Load and return the ``APIConfig`` for *api_name*.

    Reads the ``[apis.<api_name>]`` section from ``settings.toml``
    and resolves the API key from the environment.

    Raises:
        ValueError: If the section is missing from settings.toml, or if the
                    API key environment variable is not set.
    """
    settings = _load_raw_settings()
    all_apis: dict = _get_apis_dict(settings)

    if api_name not in all_apis:
        available = list(all_apis.keys())
        hint = (
            f"Available APIs: {', '.join(available)}"
            if available
            else "No APIs are configured in settings.toml."
        )
        raise ValueError(
            f"API '{api_name}' is not configured in settings.toml.\n"
            f"{hint}\n"
            "Add a [apis.<name>] section to settings.toml to register an API."
        )

    raw: dict = all_apis[api_name]

    base_url: str = raw.get("base_url", "")
    if not base_url:
        raise ValueError(
            f"[apis.{api_name}] is missing required field 'base_url'."
        )

    env_var = _env_key_for(api_name)
    api_key = os.environ.get(env_var, "")
    if not api_key:
        raise ValueError(
            f"API key for '{api_name}' not found. "
            f"Set {env_var} in your .env file."
        )

    known_keys = {"name", "base_url", "openai_compatible", "default_model", "timeout", "verify_ssl"}
    extra = {k: v for k, v in raw.items() if k not in known_keys}

    return APIConfig(
        api_name=api_name,
        display_name=raw.get("name", api_name),
        base_url=base_url,
        api_key=api_key,
        openai_compatible=raw.get("openai_compatible", False),
        default_model=raw.get("default_model"),
        timeout=int(raw.get("timeout", 30)),
        verify_ssl=bool(raw.get("verify_ssl", True)),
        extra=extra,
    )


def list_apis() -> list[str]:
    """Return the names of all APIs declared under ``[apis]`` in settings.toml."""
    settings = _load_raw_settings()
    return [k for k, v in _get_apis_dict(settings).items() if isinstance(v, dict)]


def get_default_api_name() -> Optional[str]:
    """Return the default API name from ``settings.toml``, or ``None``.

    Reads the ``default`` key from the ``[apis]`` section::

        [apis]
        default = "pu_sandbox"

    When set, bare model strings (no colon prefix) are routed to this API
    instead of the built-in Portkey/Sandbox service.
    """
    settings = _load_raw_settings()
    apis_section = settings.get("apis", {})
    if isinstance(apis_section, dict):
        return apis_section.get("default") or None
    return None


def parse_model_source(model: str) -> tuple[Optional[str], str]:
    """Split an optional ``api_name:model`` string into its parts.

    The colon separator mirrors URL syntax (``http://``) — the part before
    the first colon is the API name; everything after is the model name
    (which may itself contain slashes for provider/model notation).

    Args:
        model: A model string such as ``"della:qwen-preview"``,
               ``"pu_sandbox:openai/gpt-4o"``, or bare ``"gpt-4o"``.

    Returns:
        A ``(api_name, bare_model)`` tuple.  ``api_name`` is ``None`` when
        no colon is present.

    Examples::

        parse_model_source("della:qwen-preview")    -> ("della", "qwen-preview")
        parse_model_source("pu_sandbox:openai/gpt") -> ("pu_sandbox", "openai/gpt")
        parse_model_source("gpt-4o")                -> (None, "gpt-4o")
        parse_model_source("gpt-4o-mini")           -> (None, "gpt-4o-mini")
    """
    if ":" in model:
        api_name, _, bare_model = model.partition(":")
        api_name = api_name.strip()
        bare_model = bare_model.strip()
        if api_name and bare_model:
            return api_name, bare_model
    return None, model


def _get_apis_dict(settings: dict) -> dict:
    """Return the dict of named API entries from the ``[apis]`` section.

    Filters out scalar values (like ``default``) so only sub-table entries
    (actual API definitions) are returned.
    """
    apis_section = settings.get("apis", {})
    if not isinstance(apis_section, dict):
        return {}
    return {k: v for k, v in apis_section.items() if isinstance(v, dict)}
