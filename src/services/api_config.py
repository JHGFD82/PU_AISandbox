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

A model name with no endpoint's name in front of it always goes to the
built-in Portkey service. Reaching an endpoint always means naming it, so that
a model available in more than one place is never sent somewhere the person
did not choose — see ``parse_model_source()``. An endpoint is defined like this::

    # preferences.toml
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
    python main.py heller prompt -m qwen3.8:27b-mlx          # an error: say which endpoint
    python main.py heller prompt -m my_mac_studio:qwen3.8:27b-mlx
    python main.py heller prompt -m gpt-4o        # the built-in service
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from openai import OpenAI

from .. import settings, settings_store

# Sent as the key to an endpoint that has none, such as a model running on a
# cluster or on this computer. The OpenAI client refuses to be built with an
# empty key, and leaving it out altogether makes the client pick up
# OPENAI_API_KEY from the environment, which would send somebody's real key to
# a server that never asked for it. A server that needs no key ignores this; one
# that does need a key turns it away, which is the right thing to be told.
_NO_KEY_NEEDED = "no-key-needed"


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


# Addresses of Ollama's own interface. Ollama also speaks the OpenAI API's
# language, which is the only one the sandbox does, but at /v1 — and its
# documentation mostly shows these, so they are what somebody copies.
_OLLAMA_OWN_PATHS = ("/api/generate", "/api/chat", "/api/embed", "/api/embeddings", "/api/tags")

# The port Ollama listens on unless told otherwise.
_OLLAMA_PORT = 11434


def endpoint_address_problem(api_name: str, base_url: str) -> str | None:
    """Say what is wrong with an endpoint's address, if it is one of the recognisable mistakes.

    The sandbox adds ``/chat/completions`` to the address to send a request,
    the way every OpenAI-compatible server expects. Two mistakes turn that into
    an address that does not exist, and the server's only reply is "404 page
    not found", which says nothing about why:

    - Ollama's own address (``http://localhost:11434/api/generate``), which is
      what Ollama's documentation mostly shows, instead of the one it offers
      for OpenAI-style requests (``http://localhost:11434/v1``).
    - The full address of a request (``.../v1/chat/completions``) instead of
      the part before it.

    Args:
        api_name: The endpoint's name, for the message.
        base_url: Its ``base_url`` setting.

    Returns:
        A message saying what to change ``base_url`` to, or ``None`` if the
        address looks right.
    """
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(base_url.strip())
    path = parts.path.rstrip("/")
    fixed_path: str | None = None
    why = ""

    for own in _OLLAMA_OWN_PATHS:
        if path.endswith(own):
            fixed_path = path[: -len(own)] + "/v1"
            why = (
                "That is Ollama's own address, which the sandbox cannot talk to. Ollama "
                "also answers the way OpenAI's servers do, which is the only way the "
                "sandbox can, at an address ending in /v1."
            )
            break
    else:
        if path.endswith("/chat/completions"):
            fixed_path = path[: -len("/chat/completions")]
            why = (
                "That is the address of a single request rather than of the endpoint. "
                "The sandbox adds /chat/completions itself."
            )
        elif parts.port == _OLLAMA_PORT and path in ("", "/api"):
            fixed_path = "/v1"
            why = (
                "That looks like Ollama, which answers the way OpenAI's servers do — the "
                "only way the sandbox can talk — at an address ending in /v1."
            )

    if fixed_path is None:
        return None
    fixed = urlunsplit((parts.scheme, parts.netloc, fixed_path, "", ""))
    return (
        f"The address for the endpoint '{api_name}' is {base_url}. {why} Change "
        f'base_url in its settings to:\n    base_url = "{fixed}"'
    )


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
        ValueError: If the endpoint is missing from every settings layer, has
                    no ``base_url``, or has one that cannot work — see
                    ``endpoint_address_problem()``.
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
    problem = endpoint_address_problem(api_name, base_url)
    if problem:
        # Refused here, before anything is sent: sent, it fails with a bare
        # "404 page not found" that points nowhere near the setting.
        raise ValueError(problem)

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


def endpoint_client(api_config: APIConfig, timeout: float | None = None) -> OpenAI:
    """Build the connection used to talk to one endpoint, with its own settings applied.

    Everything that reaches an endpoint comes through here: requests made
    during a job, asking the endpoint which models it runs, and testing what a
    model can do. Building the connection in one place means a setting such as
    ``verify_ssl`` cannot be honoured by one of those and ignored by another.

    Args:
        api_config: The endpoint's definition and credential, from
                    ``load_api_config()``.
        timeout: How many seconds to wait before giving up, when something
                 wants a shorter or longer wait than the endpoint's own
                 ``timeout`` setting. ``None`` uses that setting.

    Returns:
        A client pointed at the endpoint's address, carrying its key if it has
        one.
    """
    client_options: dict = {}
    if not api_config.verify_ssl:
        # Turning certificate checking off is occasionally the only way to
        # reach a cluster with an internal certificate, so it is offered; it is
        # worth saying out loud when it happens, because it is a real weakening.
        import httpx

        logging.warning(
            "Certificate checking is turned off for the endpoint '%s'. Anything "
            "between this computer and %s could read or alter what is sent.",
            api_config.api_name, api_config.base_url,
        )
        client_options["http_client"] = httpx.Client(verify=False)

    return OpenAI(
        # See _NO_KEY_NEEDED.
        api_key=api_config.api_key or _NO_KEY_NEEDED,
        base_url=api_config.base_url,
        timeout=float(timeout if timeout is not None else api_config.timeout),
        **client_options,
    )


def list_apis() -> list[str]:
    """Return the names of all endpoints declared in the settings layers."""
    return list(settings.ENDPOINTS.keys())


def parse_model_source(model: str) -> tuple[str | None, str]:
    """Split an optional ``api_name:model`` string into its parts.

    The colon separator mirrors URL syntax — the part before the first colon
    is the endpoint name; everything after is the model name, which may itself
    contain slashes (``meta-llama/Llama-3-70B``) or further colons. Ollama names
    every model with a colon of its own, so ``my_mac_studio:qwen3.8:27b-mlx``
    asks ``my_mac_studio`` for ``qwen3.8:27b-mlx``.

    A name with no colon is always for the built-in service, even when an
    endpoint runs a model by the same name. Reaching an endpoint always means
    naming it, so nothing is sent somewhere the person did not choose.

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
        parse_model_source("my_mac_studio:qwen3:8b")     -> ("my_mac_studio", "qwen3:8b")
    """
    if ":" in model:
        api_name, _, bare_model = model.partition(":")
        api_name = api_name.strip()
        bare_model = bare_model.strip()
        if api_name and bare_model:
            return api_name, bare_model
    return None, model
