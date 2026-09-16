"""Configuration for alternate AI API endpoints.

Endpoint *definitions* (base URL, timeout, whether it's OpenAI-compatible,
etc.) live in the ``settings.*.toml`` layering — see ``src/settings.py``'s
``ENDPOINTS`` (merged from ``settings.default.toml`` -> an optional
shared file -> ``preferences.toml``, same as every other setting).
Each key under ``ENDPOINTS`` becomes the identifier used in colon syntax on
the CLI (e.g. ``-m hpc_cluster:llama-3-70b``).

The *credential* may go in either of two places: beside the rest of that
endpoint's settings, or on its own in ``settings.toml`` (see
``src/settings_store.py``) at ``endpoints.<name>.key``. ``settings.toml``
belongs to this installation alone and is never shared or layered, so a
credential there wins over one in a file a group follows.

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

Then the credential, if the endpoint asks for one — a model running on a
cluster or on this computer usually doesn't, and then there is nothing more to
add. It can go beside the settings above, or on its own in settings.toml, which
is this installation's alone and never shared:

    [endpoints.hpc_cluster]
    key = "sk-..."

Either place works — see `endpoint_credential()` below, which is what reads it
and what the settings page asks when it reports whether one is set.

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


def endpoint_name_from_credential_path(path: str) -> str | None:
    """Return the endpoint a credential path belongs to, or ``None`` if it is not one.

    The inverse of ``credential_path_for_endpoint()``, and kept beside it so
    that the two cannot come to disagree about the shape of the path.

    Args:
        path: A dotted settings path, which may name anything at all.

    Returns:
        The endpoint's name, or ``None`` when *path* does not name an
        endpoint's credential.
    """
    parts = path.split(".")
    if len(parts) == 3 and parts[0] == "endpoints" and parts[2] == "key":
        return parts[1]
    return None


def endpoint_credential(api_name: str) -> str:
    """Return one endpoint's credential, from whichever of the two places it was put.

    An endpoint's credential may be written in ``settings.toml`` on its own, or
    beside the rest of that endpoint's settings in ``preferences.toml`` or a
    shared file. Both work, and the interface offers the second, so anything
    asking whether an endpoint has a credential has to look in both — asking
    only about ``settings.toml`` is how an endpoint that works perfectly came to
    be shown as having no credential at all.

    ``settings.toml`` is looked at first because it belongs to this installation
    alone and is never shared or layered, so a personal credential there
    overrides a group's without anybody having to arrange it.

    Args:
        api_name: The endpoint's name (e.g. ``'hpc_cluster'``).

    Returns:
        The credential, or an empty string if there isn't one anywhere. An
        endpoint that isn't configured at all also answers with an empty
        string, since it has no credential either.
    """
    raw: dict = settings.ENDPOINTS.get(api_name) or {}
    return (
        settings_store.get_value(credential_path_for_endpoint(api_name))
        or str(raw.get("key", "") or "")
    )


@dataclass
class APIConfig:
    """Configuration for a single AI API endpoint.

    Attributes:
        api_name:           The endpoint key (e.g. ``hpc_cluster``).
        display_name:       Human-readable name shown in logs and --list-apis output.
        base_url:           The root URL for the API (e.g. ``https://example.com/v1``).
        api_key:            The resolved credential, from wherever it was put
                            — see ``endpoint_credential()``. Empty for an
                            endpoint that needs none, such as a model running
                            on a cluster or on this computer.
        openai_compatible:  Whether this endpoint speaks the OpenAI API's
                            language, which nearly every self-hosted server and
                            provider does. True unless said otherwise, since it
                            is the only kind the sandbox can talk to; setting it
                            False is a way of saying "this one doesn't", and the
                            sandbox then refuses it plainly rather than trying
                            and failing in a way that looks like the endpoint's
                            fault.
        default_model:      Default model name for OpenAI-compatible endpoints.
        timeout:            Request timeout in seconds.
        verify_ssl:         Whether to check the endpoint's certificate. True
                            unless said otherwise. Turning it off is sometimes
                            the only way to reach a cluster with an internal
                            certificate; it is a real weakening, so the sandbox
                            says so in the log each time it connects.
    """

    api_name: str
    display_name: str
    base_url: str
    api_key: str
    openai_compatible: bool = True
    default_model: str | None = None
    timeout: int = 30
    verify_ssl: bool = True
    extra: dict = field(default_factory=dict)


def load_api_config(api_name: str) -> APIConfig:
    """Load and return the ``APIConfig`` for *api_name*.

    Combines the endpoint's definition (from the merged ``settings.*.toml``
    layers) with its credential (from either place it is allowed to be — see
    ``endpoint_credential()``). An endpoint with no credential anywhere is
    loaded all the same, since a model running on a cluster or on this computer
    usually needs none.

    Raises:
        ValueError: If the endpoint is missing from every settings layer, or
                    has no ``base_url``.
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
            "Add an [endpoints.<name>] table to your preferences.toml, or to "
            "the shared settings file your group follows."
        )

    raw: dict = endpoints[api_name]

    base_url: str = raw.get("base_url", "")
    if not base_url:
        raise ValueError(
            f"Endpoint '{api_name}' is missing required field 'base_url'."
        )

    # Either of the two places it may have been put — see endpoint_credential(),
    # which is also what the settings page and `settings list` ask, so that what
    # they report and what actually happens here cannot come apart. None at all
    # is an ordinary answer: a model running on a cluster or on this computer
    # usually asks for no key, and one that does says so when it is reached.
    api_key = endpoint_credential(api_name)

    known_keys = {"name", "base_url", "openai_compatible", "default_model",
                  "timeout", "verify_ssl", "key"}
    extra = {k: v for k, v in raw.items() if k not in known_keys}

    return APIConfig(
        api_name=api_name,
        display_name=raw.get("name", api_name),
        base_url=base_url,
        api_key=api_key,
        openai_compatible=raw.get("openai_compatible", True),
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
