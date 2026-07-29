"""Configuration for alternate AI API endpoints.

Endpoint *definitions* (base URL, timeout, whether it's OpenAI-compatible,
etc.) live in the ``settings.*.toml`` layering — see ``src/settings.py``'s
``ENDPOINTS`` (merged from ``settings.default.toml`` -> an optional
shared file -> ``preferences.toml``, same as every other setting).
Each key under ``ENDPOINTS`` becomes the identifier used in colon syntax on
the CLI (e.g. ``-m hpc_cluster:llama-3-70b``).

The *credential* for each endpoint is kept separate, in ``settings.toml`` (see
``src/settings_store.py``) at ``endpoints.<name>.key`` — credentials are
never meant to be shared or layered the way definitions are.

Set ``[config] default_endpoint`` in any settings layer to route bare model
names to a specific endpoint instead of the built-in Portkey service::

    # preferences.toml
    [config]
    default_endpoint = "hpc_cluster"

    [endpoints.hpc_cluster]
    name = "HPC Cluster"
    base_url = "http://my-cluster.internal:8000/v1"
    openai_compatible = true
    default_model = "llama-3-70b-instruct"

Then, separately, in settings.toml (via `python main.py env set endpoints.hpc_cluster.key`):

    [endpoints.hpc_cluster]
    key = "sk-..."

Colon syntax on the CLI::

    python main.py heller prompt -m hpc_cluster:llama-3-70b
    python main.py heller prompt -m cloud_provider:model-name
    python main.py heller prompt -m llama-3-70b   # uses "default_endpoint" if set
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .. import settings, settings_store


def credential_path_for_endpoint(api_name: str) -> str:
    """Return the dotted ``settings.toml`` path holding the credential for *api_name*.

    Examples::

        credential_path_for_endpoint("hpc_cluster")   -> "endpoints.hpc_cluster.key"
    """
    return f"endpoints.{api_name}.key"


@dataclass
class APIConfig:
    """Configuration for a single AI API endpoint.

    Attributes:
        api_name:           The endpoint key (e.g. ``hpc_cluster``).
        display_name:       Human-readable name shown in logs and --list-apis output.
        base_url:           The root URL for the API (e.g. ``https://example.com/v1``).
        api_key:            Resolved API key (from ``settings.toml``).
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
    default_model: str | None = None
    timeout: int = 30
    verify_ssl: bool = True
    extra: dict = field(default_factory=dict)


def load_api_config(api_name: str) -> APIConfig:
    """Load and return the ``APIConfig`` for *api_name*.

    Combines the endpoint's definition (from the merged ``settings.*.toml``
    layers) with its credential (from ``settings.toml``).

    Raises:
        ValueError: If the endpoint is missing from every settings layer, or
                    if its credential isn't set in ``settings.toml``.
    """
    endpoints: dict = settings.ENDPOINTS

    if api_name not in endpoints:
        available = list(endpoints.keys())
        hint = (
            f"Available endpoints: {', '.join(available)}"
            if available
            else "No endpoints are configured."
        )
        raise ValueError(
            f"API endpoint '{api_name}' is not configured.\n"
            f"{hint}\n"
            "Add an [endpoints.<name>] table to settings.default.toml, a shared "
            "settings file, or your preferences.toml to register an endpoint."
        )

    raw: dict = endpoints[api_name]

    base_url: str = raw.get("base_url", "")
    if not base_url:
        raise ValueError(
            f"Endpoint '{api_name}' is missing required field 'base_url'."
        )

    credential_path = credential_path_for_endpoint(api_name)
    api_key = settings_store.get_value(credential_path) or ""
    if not api_key:
        raise ValueError(
            f"API key for '{api_name}' not found. "
            f"Set it with: python main.py env set {credential_path}"
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
    """Return the names of all endpoints declared in the settings layers."""
    return list(settings.ENDPOINTS.keys())


def get_default_api_name() -> str | None:
    """Return the default endpoint name, or ``None``.

    Reads ``[config] default_endpoint`` from the merged settings layers.
    When set, bare model strings (no colon prefix) are routed to this
    endpoint instead of the built-in Portkey service.
    """
    return settings.DEFAULT_ENDPOINT


def parse_model_source(model: str) -> tuple[str | None, str]:
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
