"""Makes the sandbox answer only to the names this computer goes by.

A sandbox listening on ``127.0.0.1`` can't be reached from another computer,
but it can be reached from a web page open on this one, through a trick called
DNS rebinding. A website the person happens to visit points its own name, after
the page has loaded, at ``127.0.0.1``. The browser now treats the sandbox as
part of that website, and the website's page can use it as freely as the
person can: with no passphrase set, unlock it, read conversations, spend the
API budget, install a plugin. Every request really does come from this
computer, so checking where a request comes from does not catch it.

What gives it away is the name the browser was asked to open. The person's own
browser asks for ``127.0.0.1`` or ``localhost``; the website's page asks for
the website's own name. So while the sandbox listens only on this computer, a
request for any other name is refused. Each request names it in its ``Host``
header.

Listening for other computers is different: they reach the sandbox by
whatever name or address the network gives this one, which can't be known
here. That is only allowed with a passphrase set (see ``_serve`` in
``plugin.py``), and a website can't get past a passphrase it doesn't know, so
no name is refused then.

Registered into ``sys.modules`` as ``_pu_webui_hostnames`` by ``plugin.py``,
the same flat, dot-free name every one of this plugin's own files uses — the
reason is explained at the top of ``app.py``.
"""

from __future__ import annotations

import ipaddress
from typing import Optional
from urllib.parse import urlsplit

from fastapi import FastAPI
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

_REFUSED = (
    "This address isn't one the sandbox answers to. Open it as "
    "http://127.0.0.1 (with its port) instead."
)


def is_loopback(host: str) -> bool:
    """Return whether *host* is a name or address only this computer can use.

    Args:
        host: A name or address such as ``127.0.0.1``, ``localhost`` or
              ``::1``, with or without square brackets around it.

    Returns:
        True if it can only ever mean this computer.
    """
    name = host.strip().lower().strip("[]")
    if name == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def name_asked_for(host_header: str) -> Optional[str]:
    """Return the name in a ``Host`` header, without its port.

    Args:
        host_header: As the browser sent it, e.g. ``127.0.0.1:8000`` or
                     ``[::1]:8000``.

    Returns:
        The name or address (``127.0.0.1``, ``::1``), or None if the header
        is empty or isn't a name at all.
    """
    try:
        return urlsplit("//" + host_header.strip()).hostname
    except ValueError:
        return None


class _OnlyThisComputersNames:
    """Refuses any request that names something other than this computer."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            host = ""
            for key, value in scope.get("headers", []):
                if key == b"host":
                    host = value.decode("latin-1")
                    break
            name = name_asked_for(host)
            if name is None or not is_loopback(name):
                await PlainTextResponse(_REFUSED, status_code=400)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def answer_only_to_this_computer(app: FastAPI, listening_on: str) -> None:
    """Make *app* refuse requests for other names, while it listens only here.

    Call it after every other ``add_middleware()``, so it is the first thing a
    request meets and nothing behind it — the unlock cookie included — is
    reached by a refused request.

    Args:
        app: Either of this plugin's two applications.
        listening_on: The address the server listens on. Anything other than
            this computer's own leaves *app* unchanged — see this module's
            docstring for why.
    """
    if is_loopback(listening_on):
        app.add_middleware(_OnlyThisComputersNames)
