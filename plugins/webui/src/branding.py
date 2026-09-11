"""Serves the sandbox's mark to the browser's tab.

A browser asks for ``/favicon.ico`` on its own, before any page has told it
to, so the mark has to exist at that exact address rather than only inside
the pages that mention it. That is the whole of this file: one small file on
disk and the address it answers at.

It lives apart from both of the applications that use it because there are
two — the setup pages people see once (``setup_web.py``) and the sandbox
itself (``app.py``) are separate FastAPI applications with separate
addresses, and a tab showing the mark on one and nothing on the other looks
like two different pieces of software. Each calls ``add_favicon_route()`` on
itself.

Registered into ``sys.modules`` as ``_pu_webui_branding`` by ``plugin.py``,
the same flat, dot-free name every one of this plugin's own files uses — the
reason is explained at the top of ``app.py``.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

# Drawn from the same artwork as the mark behind the page's title, at the
# three sizes a browser picks between (16, 32 and 48 pixels across).
FAVICON_PATH = Path(__file__).parent / "static" / "favicon.ico"


def add_favicon_route(app: FastAPI) -> None:
    """Give *app* an address the browser can fetch the tab's icon from.

    Deliberately open to anyone who can reach the page, unlocked or not: the
    unlock screen is itself a page in a tab, and an icon is not a secret.

    Args:
        app: The web application to add the address to. Both of this
             plugin's applications call this on themselves.
    """

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> FileResponse:
        return FileResponse(FAVICON_PATH, media_type="image/x-icon")
