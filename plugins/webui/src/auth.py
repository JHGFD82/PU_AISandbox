"""Authentication for the webui plugin: one shared local unlock gate.

There is no per-professor login here — see docs/webui-plugin-plan.md section 4
for the full reasoning. This module defines the pluggable ``AuthBackend``
contract and ships one working implementation, ``PassphraseBackend``. A
second implementation, backed by Princeton's Central Authentication Service
(CAS), is documented in the plan but intentionally not built yet — CAS
requires a real HTTPS hostname registered with OIT before it will work at
all, which this project doesn't have set up. Nothing about this module needs
to change when that backend is added later; ``app.py`` only ever talks to
whatever object satisfies ``AuthBackend``.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

import bcrypt
from starlette.requests import Request

from src import settings_store

# bcrypt's underlying algorithm only ever looks at the first 72 bytes of a
# password — anything past that is silently ignored by the hash, not an
# error. Older bcrypt releases (and the passlib wrapper this module used to
# go through) truncated automatically; bcrypt 5+ raises instead. Truncating
# ourselves here keeps behavior identical either way rather than depending
# on which bcrypt version happens to be installed.
_BCRYPT_MAX_BYTES = 72


def _prepare(passphrase: str) -> bytes:
    """Encode a passphrase for bcrypt, truncated to the 72 bytes bcrypt actually uses."""
    return passphrase.encode("utf-8")[:_BCRYPT_MAX_BYTES]


@runtime_checkable
class AuthBackend(Protocol):
    """The contract any unlock-gate implementation must satisfy.

    A backend decides whether one incoming request is allowed to unlock the
    application. It receives the whole request (not just a passphrase)
    because different backends need different things from it — a passphrase
    backend reads a submitted form field, while a future CAS backend would
    read a redirect ticket from the query string instead. Neither
    ``app.py`` nor the session-handling code needs to know which kind of
    backend is configured.
    """

    async def authenticate(self, request: Request) -> bool:
        """Return True if *request* proves the caller is allowed in."""
        ...


class PassphraseBackend:
    """Checks a submitted passphrase against a bcrypt hash.

    The hash is normally read from ``.settings`` at ``webui.passphrase_hash``
    (set by running ``python main.py webui set-passphrase``). If that value
    is unset or empty, this backend treats every request as already
    authenticated — this is the documented "no gate" mode meant for a
    strictly ``127.0.0.1``-only setup (see docs/webui-plugin-plan.md section 8).
    """

    def __init__(self, passphrase_hash: str | None = None) -> None:
        """Create the backend, optionally overriding where the hash comes from (used by tests).

        Args:
            passphrase_hash: The bcrypt hash to check submissions against.
                              ``None`` (the normal case) reads it from
                              ``.settings`` instead.
        """
        self._hash = (
            passphrase_hash
            if passphrase_hash is not None
            else (settings_store.get_value("webui.passphrase_hash") or "")
        )

    @property
    def configured(self) -> bool:
        """True if a passphrase hash is set, meaning the gate is actually active."""
        return bool(self._hash)

    async def authenticate(self, request: Request) -> bool:
        """Check the submitted 'passphrase' form field against the configured hash.

        Args:
            request: The incoming ``POST /unlock`` request. Its form body is
                     read for a ``passphrase`` field.

        Returns:
            ``True`` if no passphrase is configured at all (open-access
            mode), or if the submitted passphrase matches the configured
            hash. ``False`` otherwise.
        """
        if not self.configured:
            return True
        form = await request.form()
        submitted = form.get("passphrase", "")
        if not submitted:
            return False
        try:
            return bcrypt.checkpw(_prepare(str(submitted)), self._hash.encode("utf-8"))
        except ValueError:
            # Malformed/foreign hash format — treat as "does not match" rather
            # than crashing the request.
            return False


def hash_passphrase(passphrase: str) -> str:
    """Hash a plaintext passphrase for storage at ``.settings``'s ``webui.passphrase_hash``.

    Used by the ``webui set-passphrase`` CLI command — never called from a
    web request.

    Args:
        passphrase: The plaintext passphrase a person typed at the terminal.

    Returns:
        A bcrypt hash string.
    """
    return bcrypt.hashpw(_prepare(passphrase), bcrypt.gensalt()).decode("utf-8")


def get_configured_backend() -> PassphraseBackend:
    """Return the auth backend to use for this run.

    Always returns a ``PassphraseBackend`` today. This is the one place that
    will need to change to select ``CasBackend`` instead once it's built
    (e.g. based on a ``[webui] auth_backend`` setting) — see
    docs/webui-plugin-plan.md section 4.
    """
    return PassphraseBackend()
