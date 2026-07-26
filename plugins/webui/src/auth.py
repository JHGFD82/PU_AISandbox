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

import threading
import time
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

# How many wrong passphrases one computer may try before it has to wait, and
# how long that wait is. Generous enough that a professor mistyping their own
# passphrase a few times never notices it exists, while making it impractical
# to work through a list of guesses: five tries per two minutes is roughly
# 3,600 guesses a day, against a passphrase with vastly more possibilities
# than that.
_MAX_ATTEMPTS = 5
_LOCKOUT_SECONDS = 120


class AttemptLimiter:
    """Slows down repeated wrong passphrases from the same computer.

    Without this, nothing stops a program from trying passphrases as fast as
    the server can check them until it finds the right one. Guesses are
    counted per network address; once a computer has used up its allowance it
    is refused for a short cooling-off period, whether or not its next guess
    would have been correct. Getting in successfully clears that computer's
    count.

    Counts are kept in memory only, so restarting the server forgets them —
    acceptable here, because restarting requires access to the machine the
    server runs on, which is a bigger privilege than the gate protects.
    """

    def __init__(self, max_attempts: int = _MAX_ATTEMPTS, lockout_seconds: int = _LOCKOUT_SECONDS) -> None:
        """Create a limiter.

        Args:
            max_attempts: Wrong guesses allowed before the cooling-off period.
            lockout_seconds: How long a computer must wait once it has used
                             up its allowance.
        """
        self._max_attempts = max_attempts
        self._lockout_seconds = lockout_seconds
        # client address -> (wrong guesses so far, time of the most recent one)
        self._attempts: dict[str, tuple[int, float]] = {}
        # The server handles requests on several threads at once, so this
        # turn-taking lock stops two simultaneous guesses from reading and
        # updating the same count at the same moment and losing one of them.
        self._lock = threading.Lock()

    def _client_key(self, request: Request) -> str:
        """Return an identifier for the computer making *request*."""
        client = getattr(request, "client", None)
        return getattr(client, "host", None) or "unknown"

    def seconds_remaining(self, request: Request) -> int:
        """Return how long this computer must still wait, or 0 if it may try now.

        Args:
            request: The incoming unlock request.

        Returns:
            Whole seconds left in the cooling-off period; ``0`` when the
            caller is free to attempt an unlock.
        """
        key = self._client_key(request)
        with self._lock:
            count, last_attempt = self._attempts.get(key, (0, 0.0))
            if count < self._max_attempts:
                return 0
            elapsed = time.monotonic() - last_attempt
            if elapsed >= self._lockout_seconds:
                # Cooling-off period served — wipe the slate clean.
                self._attempts.pop(key, None)
                return 0
            return int(self._lockout_seconds - elapsed) + 1

    def record_failure(self, request: Request) -> None:
        """Count one wrong guess from this computer."""
        key = self._client_key(request)
        with self._lock:
            count, _ = self._attempts.get(key, (0, 0.0))
            self._attempts[key] = (count + 1, time.monotonic())

    def record_success(self, request: Request) -> None:
        """Forget this computer's wrong guesses after a successful unlock."""
        key = self._client_key(request)
        with self._lock:
            self._attempts.pop(key, None)


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
