"""Configuration for alternate AI API endpoints.

Endpoint definitions live in ``apis.json`` at the repository root.  Each
key inside ``"endpoints"`` becomes the identifier used in colon syntax on
the CLI (e.g. ``-m hpc_cluster:llama-3-70b``).

Set ``"default"`` to route bare model names to a specific endpoint
instead of the built-in Portkey service::

    {
      "default": "hpc_cluster",
      "endpoints": {
        "hpc_cluster": {
          "name": "HPC Cluster",
          "base_url": "http://my-cluster.internal:8000/v1",
          "openai_compatible": true,
          "default_model": "llama-3-70b-instruct"
        }
      }
    }

The API key for each endpoint is read from the environment variable
``API_<UPPERCASE_NAME>_KEY`` (e.g. ``API_HPC_CLUSTER_KEY``).

Colon syntax on the CLI::

    python main.py heller prompt -m hpc_cluster:llama-3-70b
    python main.py heller prompt -m cloud_provider:model-name
    python main.py heller prompt -m llama-3-70b   # uses "default" if set
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).parent.parent.parent  # src/services/ -> repo root
_APIS_JSON_PATH = _ROOT / "apis.json"


def _load_apis_json() -> dict:
    """Load and return the parsed contents of ``apis.json``.

    Returns an empty dict when the file is absent.
    """
    if not _APIS_JSON_PATH.exists():
        return {}
    with _APIS_JSON_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


@dataclass
class APIConfig:
    """Configuration for a single AI API endpoint.

    Attributes:
        api_name:           The endpoint key from ``apis.json`` (e.g. ``hpc_cluster``).
        display_name:       Human-readable name shown in logs and --list-apis output.
        base_url:           The root URL for the API (e.g. ``https://example.com/v1``).
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
    """Return the env-var name for the given endpoint name.

    Examples::

        _env_key_for("hpc_cluster")   -> "API_HPC_CLUSTER_KEY"
        _env_key_for("cloud-provider") -> "API_CLOUD_PROVIDER_KEY"
    """
    safe = api_name.upper().replace("-", "_")
    return f"API_{safe}_KEY"


def load_api_config(api_name: str) -> APIConfig:
    """Load and return the ``APIConfig`` for *api_name*.

    Reads the matching entry from ``apis.json`` and resolves the API key
    from the environment.

    Raises:
        ValueError: If the endpoint is missing from ``apis.json``, or if the
                    API key environment variable is not set.
    """
    data = _load_apis_json()
    endpoints: dict = data.get("endpoints", {})

    if api_name not in endpoints:
        available = list(endpoints.keys())
        hint = (
            f"Available endpoints: {', '.join(available)}"
            if available
            else "No endpoints are configured in apis.json."
        )
        raise ValueError(
            f"API endpoint '{api_name}' is not configured in apis.json.\n"
            f"{hint}\n"
            "Add an entry under \"endpoints\" in apis.json to register an endpoint."
        )

    raw: dict = endpoints[api_name]

    base_url: str = raw.get("base_url", "")
    if not base_url:
        raise ValueError(
            f"apis.json entry '{api_name}' is missing required field 'base_url'."
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
    """Return the names of all endpoints declared in ``apis.json``."""
    data = _load_apis_json()
    return list(data.get("endpoints", {}).keys())


def get_default_api_name() -> Optional[str]:
    """Return the default endpoint name from ``apis.json``, or ``None``.

    Reads the top-level ``"default"`` key::

        { "default": "hpc_cluster", "endpoints": { ... } }

    When set, bare model strings (no colon prefix) are routed to this endpoint
    instead of the built-in Portkey service.
    """
    data = _load_apis_json()
    return data.get("default") or None


def parse_model_source(model: str) -> tuple[Optional[str], str]:
    """Split an optional ``api_name:model`` string into its parts.

    The colon separator mirrors URL syntax — the part before the first colon
    is the endpoint name; everything after is the model name (which may itself
    contain slashes for provider/model notation).

    Args:
        model: A model string such as ``"hpc_cluster:llama-3-70b"``,
               ``"cloud_provider:model-name"``, or bare ``"gpt-4o"``.

    Returns:
        A ``(api_name, bare_model)`` tuple.  ``api_name`` is ``None`` when
        no colon is present.

    Examples::

        parse_model_source("hpc_cluster:llama-3-70b")    -> ("hpc_cluster", "llama-3-70b")
        parse_model_source("cloud_provider:model-name")  -> ("cloud_provider", "model-name")
        parse_model_source("gpt-4o")                     -> (None, "gpt-4o")
        parse_model_source("gpt-4o-mini")                -> (None, "gpt-4o-mini")
    """
    if ":" in model:
        api_name, _, bare_model = model.partition(":")
        api_name = api_name.strip()
        bare_model = bare_model.strip()
        if api_name and bare_model:
            return api_name, bare_model
    return None, model
