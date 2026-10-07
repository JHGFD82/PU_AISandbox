"""Tests for plugins/webui/src/app.py's spending routes: what the sidebar is told about each person's usage."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from plugins.webui.tests.helpers import (
    WEBUI_SRC,
    _rendered_chat,
)

# Every test here runs as two fake professors with no passphrase set; see conftest.py.
pytestmark = pytest.mark.usefixtures("_configured_professors", "_no_passphrase")


class TestUsageEndpoint:
    """The spend sidebar's data, which had no test coverage at all.

    The budget figures here must be the ones TokenTracker works out, not a
    second copy of the arithmetic — an earlier version computed its own and
    had already drifted from the terminal report.
    """

    @pytest.fixture
    def tracker(self, monkeypatch):
        """Install a stand-in TokenTracker and hand it back for assertions."""
        app_module = sys.modules["_pu_webui_app"]
        fake = MagicMock()
        fake.monthly_limit = 50.0
        fake.usage_data = {"model_usage": {"gpt-4o": {"total_cost": 12.5}}}
        fake.get_all_time_usage.return_value = {"total_cost": 99.0}
        fake.get_monthly_budget_status.return_value = {
            "monthly_usage": {"total_cost": 12.5, "total_tokens": 4000},
            "usage_percentage": 25.0,
            "remaining_budget": 37.5,
            "is_exceeded": False,
            "approaching_limit": False,
        }
        monkeypatch.setattr(app_module, "TokenTracker", lambda professor: fake)
        return fake

    def test_returns_the_trackers_budget_figures(self, unlocked_client, tracker):
        resp = unlocked_client.get("/api/usage", params={"professor": "heller"})
        assert resp.status_code == 200
        budget = resp.json()["budget"]
        assert budget["monthly_limit"] == 50.0
        assert budget["usage_percentage"] == 25.0
        assert budget["remaining_budget"] == 37.5

    def test_includes_the_two_warning_flags(self, unlocked_client, tracker):
        """These were missing entirely, so the sidebar couldn't show "over budget"."""
        tracker.get_monthly_budget_status.return_value |= {
            "usage_percentage": 130.0,
            "remaining_budget": 0.0,
            "is_exceeded": True,
            "approaching_limit": True,
        }
        budget = unlocked_client.get(
            "/api/usage", params={"professor": "heller"}
        ).json()["budget"]
        assert budget["is_exceeded"] is True
        assert budget["approaching_limit"] is True

    def test_month_totals_come_from_the_same_call_as_the_budget(self, unlocked_client, tracker):
        """One question, asked once — the month shown and the budget it's
        measured against can't disagree if they came from the same answer."""
        body = unlocked_client.get("/api/usage", params={"professor": "heller"}).json()
        assert body["month"] == {"total_cost": 12.5, "total_tokens": 4000}
        assert body["all_time"] == {"total_cost": 99.0}
        assert body["model_usage"] == {"gpt-4o": {"total_cost": 12.5}}
        tracker.get_monthly_budget_status.assert_called_once()

    def test_requires_unlock(self, client, tracker):
        assert client.get("/api/usage", params={"professor": "heller"}).status_code == 401

    def test_rejects_unknown_professor(self, unlocked_client, tracker):
        """The name reaches file paths, so it must name a configured professor."""
        resp = unlocked_client.get("/api/usage", params={"professor": "nobody"})
        assert resp.status_code == 400


class TestEndpointUsageInTheSidebar:
    """Usage from someone's own AI service, shown apart from Princeton spending."""

    def _page(self):

        from fastapi.templating import Jinja2Templates

        here = WEBUI_SRC / "templates"
        return (Jinja2Templates(directory=str(here)).env
                .get_template("chat.html").render(request=None))

    def test_the_api_reports_endpoint_totals(self, unlocked_client, tmp_path, monkeypatch):
        from unittest.mock import patch

        from src.tracking.token_tracker import TokenTracker

        tracker = TokenTracker(
            "heller", data_file=str(tmp_path / "u.json"), monthly_limit=10.0
        )
        with patch("src.tracking.token_tracker.get_pricing_unit", return_value=1_000_000), \
             patch("src.tracking.token_tracker.get_model_pricing",
                   return_value={"input": 1.0, "output": 1.0}):
            tracker.record_usage("llama-3-70b", 100, 50, 150, endpoint="my_cluster")
        import sys

        monkeypatch.setattr(sys.modules["_pu_webui_app"], "TokenTracker", lambda **kw: tracker)
        data = unlocked_client.get("/api/usage?professor=heller").json()
        assert data["endpoint_usage"]["my_cluster"]["total_usage"]["total_tokens"] == 150

    def test_the_section_is_hidden_until_there_is_something_in_it(self):
        """Almost everyone is on the sandbox alone and should see no change."""
        page = self._page()
        assert '<div id="spend-endpoints-section" hidden>' in page
        # And stays hidden after the figures load — the markup alone would let
        # an empty heading appear the moment the sidebar refreshed.
        assert "section.hidden = endpoints.length === 0;" in page

    def test_it_shows_tokens_and_never_money(self):
        page = self._page()
        # Just the loop that draws these rows — the rest of the sidebar shows
        # Princeton spending and is supposed to show money.
        block = page[page.index("const endpoints = Object.entries"):]
        block = block[:block.index("list.appendChild(row);")]
        assert "tokens" in block
        assert "fmtMoney" not in block, "a cost was shown for a service with no known prices"

    def test_it_says_these_are_not_billed_through_the_sandbox(self):
        page = self._page()
        assert "Counted, not costed" in page


class TestTheSidebarSaysWhenAFolderCannotBeRead:
    """A total that quietly leaves somebody's shared folder out looks exactly
    like a total for somebody who has not spent anything."""

    def _configured(self, tmp_path, monkeypatch, *, reachable):
        from src import settings_store
        from src.tracking import token_tracker

        monkeypatch.setattr(settings_store, "SETTINGS_PATH", tmp_path / "settings.toml")
        monkeypatch.setattr(token_tracker, "data_root", lambda: tmp_path / "data")
        (tmp_path / "data").mkdir(exist_ok=True)
        settings_store.add_professor("heller", "Heller", "sk-test")
        folder = tmp_path / ("mounted" if reachable else "not-mounted")
        if reachable:
            folder.mkdir()
        settings_store.set_professor_usage_source(
            "heller", str(folder), mode="shared-write")
        return folder

    def test_it_names_the_folder(self, unlocked_client, tmp_path, monkeypatch):
        folder = self._configured(tmp_path, monkeypatch, reachable=False)
        body = unlocked_client.get("/api/usage?professor=heller").json()
        assert body["unreadable_folders"] == [str(folder)]

    def test_and_says_nothing_when_it_is_there(self, unlocked_client, tmp_path, monkeypatch):
        self._configured(tmp_path, monkeypatch, reachable=True)
        body = unlocked_client.get("/api/usage?professor=heller").json()
        assert body["unreadable_folders"] == []

    def test_somebody_elses_missing_folder_is_not_reported_here(
        self, unlocked_client, tmp_path, monkeypatch
    ):
        from src import settings_store

        self._configured(tmp_path, monkeypatch, reachable=True)
        settings_store.add_professor("conlan", "Conlan", "sk-test")
        settings_store.set_professor_usage_source(
            "conlan", str(tmp_path / "gone"), mode="shared-write")
        body = unlocked_client.get("/api/usage?professor=heller").json()
        assert body["unreadable_folders"] == []

    def test_the_figures_are_still_returned(self, unlocked_client, tmp_path, monkeypatch):
        """A warning beside the numbers, not instead of them."""
        self._configured(tmp_path, monkeypatch, reachable=False)
        body = unlocked_client.get("/api/usage?professor=heller").json()
        assert "unreadable_folders" in body
        assert len(body) > 1

    def test_the_panel_has_somewhere_to_say_it(self):
        """Returning the fact and never showing it would leave the silent
        zero exactly as it was."""
        chat = _rendered_chat()
        assert 'id="spend-unreadable"' in chat
        # Above the figures: it is about whether to believe them.
        assert chat.index('id="spend-unreadable"') < chat.index('id="spend-month"')

    def test_and_shows_it_only_when_there_is_something_to_say(self):
        chat = _rendered_chat()
        assert "unreadable.hidden = missing.length === 0" in chat

    def test_the_folder_name_is_written_as_text_not_markup(self):
        """A path is not markup, and this one comes from a settings file."""
        chat = _rendered_chat()
        # The warning block alone. Reading further reaches the by-model list,
        # which clears itself with innerHTML = "" and is entitled to.
        block = chat[chat.index("const unreadable = document.getElementById"):
                     chat.index("const byModel = document.getElementById")]
        assert "textContent" in block
        assert "innerHTML" not in block


class TestRecordingACorrectionOverHttp:
    def test_it_needs_an_unlocked_session(self, client):
        assert client.post("/api/usage/adjust", json={
            "professor": "heller", "stated_total": 5.0,
        }).status_code == 401

    def test_a_correction_is_recorded_and_the_month_reports_it(
        self, unlocked_client, monkeypatch
    ):
        recorded = {}

        class _Tracker:
            def __init__(self, professor=None, **kw):
                recorded["professor"] = professor

            def record_cost_adjustment(self, stated_total, month=None, note=""):
                recorded["stated_total"] = stated_total
                recorded["note"] = note
                return {"amount": 4.0, "stated_total": stated_total}

            def get_monthly_budget_status(self, month=None):
                return {"monthly_usage": {"total_cost": 4.0}}

        app_module = sys.modules["_pu_webui_app"]
        monkeypatch.setattr(app_module, "TokenTracker", _Tracker)

        resp = unlocked_client.post("/api/usage/adjust", json={
            "professor": "heller", "stated_total": 4.0, "note": "OIT invoice",
        })
        assert resp.status_code == 200
        assert recorded["stated_total"] == 4.0
        assert recorded["note"] == "OIT invoice"
        assert resp.json()["adjustment"]["amount"] == 4.0

    def test_a_negative_total_is_refused_with_a_reason(self, unlocked_client, monkeypatch):
        class _Tracker:
            def __init__(self, professor=None, **kw):
                pass

            def record_cost_adjustment(self, stated_total, month=None, note=""):
                raise ValueError("A month's total cannot be negative (got -5.0).")

        app_module = sys.modules["_pu_webui_app"]
        monkeypatch.setattr(app_module, "TokenTracker", _Tracker)

        resp = unlocked_client.post("/api/usage/adjust", json={
            "professor": "heller", "stated_total": -5.0,
        })
        assert resp.status_code == 400
        assert "negative" in resp.json()["detail"]
