"""Lets the sandbox's web server be stopped: by the person using it, or by the launcher.

There are two ways to stop it, and they are for two different people:

* The **Quit** button on the page (``POST /quit`` in ``app.py``) is for the
  person using the sandbox. It is behind the passphrase, like everything else
  they can do.
* ``POST /__stop`` is for ``start.py``. Double-clicking the sandbox's icon
  always starts a fresh copy — starting is when it looks for a newer version —
  so the launcher first asks whichever copy is already running to stop. It has
  no passphrase to offer, so it proves it is the launcher another way: each
  start makes up a random code (the "stop token"), keeps it in a file only this
  computer's user can read, and hands it to the server in the environment
  variable named below. A request carrying the same code in its header is the
  launcher; one without it is refused, so a web page elsewhere can't stop the
  sandbox by sending a request to it.

Both end the same way — the server is asked to finish what it is answering and
stop — and both are refused while something is still being worked on, because
a translation in progress lives only in memory and would be thrown away.

Registered into ``sys.modules`` as ``_pu_webui_stopping`` by ``plugin.py``,
the same flat, dot-free name every one of this plugin's own files uses — the
reason is explained at the top of ``app.py``.
"""

from __future__ import annotations

import hmac
import os
from typing import Callable, Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# Both names are shared with start.py, which cannot import this file (it has
# to run on Pythons too old for the sandbox), so they are written out in both.
STOP_TOKEN_VARIABLE = "PU_SANDBOX_STOP_TOKEN"
STOP_TOKEN_HEADER = "X-Sandbox-Stop-Token"


def stop_soon(app: FastAPI) -> bool:
    """Ask the server running *app* to stop once it has answered what it is answering.

    Whoever starts the server leaves it at ``app.state.server`` for this to
    find. An app with no server there (one built by a test, say) is left
    running, and this says so.

    Returns:
        True if the server was asked to stop.
    """
    server = getattr(app.state, "server", None)
    if server is None:
        return False
    server.should_exit = True
    return True


def add_stop_route(app: FastAPI,
                   reason_to_wait: Optional[Callable[[], Optional[str]]] = None) -> None:
    """Give *app* the address the launcher uses to stop it.

    Args:
        app: The web application to add ``POST /__stop`` to. Both of this
             plugin's applications — the setup pages and the sandbox itself
             — call this on themselves, so the launcher can replace either.
        reason_to_wait: Asked before stopping. Returns a sentence saying why
             now is a bad moment (a translation still running, say), or None
             if stopping is fine. Leave it out for an app with nothing to lose.

    The answers the launcher acts on: 200 means stopping; 409 means "not now",
    and the launcher opens the running copy instead; 404 (no token was given to
    this server) and 403 (the wrong one) both mean "this isn't yours to stop".
    """

    @app.post("/__stop")
    async def stop(request: Request):
        expected = os.environ.get(STOP_TOKEN_VARIABLE, "")
        if not expected:
            return JSONResponse({"error": "Not found"}, status_code=404)
        given = request.headers.get(STOP_TOKEN_HEADER, "")
        if not hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8")):
            return JSONResponse({"error": "Not allowed"}, status_code=403)
        reason = reason_to_wait() if reason_to_wait is not None else None
        if reason:
            return JSONResponse({"error": reason}, status_code=409)
        stop_soon(app)
        return JSONResponse({"ok": True})
