"""Tests for plugins/webui/src/auth.py — PassphraseBackend and helpers."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.webui.src.auth import PassphraseBackend, hash_passphrase
from src import settings_store


def _run(coro):
    """Run an async call from a plain sync test — avoids adding a pytest-asyncio dependency
    for what is otherwise this project's only async code."""
    return asyncio.run(coro)


class _FakeRequest:
    """Minimal stand-in for a Starlette Request — only needs an async form()."""

    def __init__(self, form_data: dict):
        self._form_data = form_data

    async def form(self):
        return self._form_data


class TestConfigured:
    def test_configured_true_when_hash_set(self):
        backend = PassphraseBackend(passphrase_hash="somehash")
        assert backend.configured is True

    def test_configured_false_when_hash_empty(self):
        backend = PassphraseBackend(passphrase_hash="")
        assert backend.configured is False

    def test_reads_from_settings_when_not_given(self, monkeypatch):
        monkeypatch.setattr(settings_store, "get_value", lambda path: "settings-hash")
        backend = PassphraseBackend()
        assert backend.configured is True

    def test_defaults_to_unconfigured_when_settings_unset(self, monkeypatch):
        monkeypatch.setattr(settings_store, "get_value", lambda path: None)
        backend = PassphraseBackend()
        assert backend.configured is False


class TestAuthenticate:
    def test_open_access_when_unconfigured(self):
        backend = PassphraseBackend(passphrase_hash="")
        result = _run(backend.authenticate(_FakeRequest({})))
        assert result is True

    def test_correct_passphrase_succeeds(self):
        hashed = hash_passphrase("correct horse battery staple")
        backend = PassphraseBackend(passphrase_hash=hashed)
        result = _run(backend.authenticate(_FakeRequest({"passphrase": "correct horse battery staple"})))
        assert result is True

    def test_wrong_passphrase_fails(self):
        hashed = hash_passphrase("correct horse battery staple")
        backend = PassphraseBackend(passphrase_hash=hashed)
        result = _run(backend.authenticate(_FakeRequest({"passphrase": "wrong guess"})))
        assert result is False

    def test_missing_passphrase_field_fails(self):
        hashed = hash_passphrase("secret")
        backend = PassphraseBackend(passphrase_hash=hashed)
        result = _run(backend.authenticate(_FakeRequest({})))
        assert result is False

    def test_malformed_hash_fails_gracefully(self):
        backend = PassphraseBackend(passphrase_hash="not-a-real-bcrypt-hash")
        result = _run(backend.authenticate(_FakeRequest({"passphrase": "anything"})))
        assert result is False


class TestHashPassphrase:
    def test_hash_is_not_plaintext(self):
        hashed = hash_passphrase("mypassword")
        assert hashed != "mypassword"

    def test_hash_round_trips_through_bcrypt(self):
        import bcrypt
        hashed = hash_passphrase("mypassword")
        assert bcrypt.checkpw(b"mypassword", hashed.encode("utf-8"))
