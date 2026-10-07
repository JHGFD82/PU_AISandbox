"""Tests for plugins/webui/src/app.py's model routes: adding a model from the browser, testing it again, and removing it."""

from __future__ import annotations

import json
import sys

import pytest

from plugins.webui.tests.helpers import (
    WEBUI_SRC,
)

# Every test here runs as two fake professors with no passphrase set; see conftest.py.
pytestmark = pytest.mark.usefixtures("_configured_professors", "_no_passphrase")


class TestAddingAModelFromTheBrowser:
    """Adding a model without opening a terminal or a JSON file.

    The point of the section is the testing that follows the add: a model whose
    capabilities were never established is recorded as unable to read images,
    and chat requires that — so before this, a model added anywhere but the
    command line would be offered in the picker and then refused on use.
    """

    def test_it_needs_an_unlocked_session(self, client):
        assert client.get("/api/settings/models").status_code == 401
        assert client.post("/api/settings/models", json={
            "provider_model": "openai/gpt-4o", "professor": "smith",
        }).status_code == 401

    def test_the_catalog_is_listed_with_what_each_model_can_do(
        self, unlocked_client, a_catalog
    ):
        models = unlocked_client.get("/api/settings/models").json()["models"]
        assert [m["name"] for m in models] == ["gpt-4o", "old-text-model"]
        seen = {m["name"]: m for m in models}
        assert seen["gpt-4o"]["supports_vision"] is True
        assert seen["old-text-model"]["supports_vision"] is False
        assert seen["gpt-4o"]["input"] == 2.5

    def test_a_model_nobody_has_tested_is_not_called_text_only(
        self, unlocked_client, a_catalog
    ):
        """The distinction the whole section turns on.

        old-text-model has supports_vision false because nothing ever asked,
        not because anything found out. The page needs to be able to say so.
        """
        models = {m["name"]: m for m in
                  unlocked_client.get("/api/settings/models").json()["models"]}
        assert models["old-text-model"]["tested"] is False

    def test_a_missing_catalog_is_an_empty_list_not_a_failure(
        self, unlocked_client, monkeypatch, tmp_path
    ):
        """An ordinary state on a copy that has not been set up yet.

        Pointed at a catalog that is not there, rather than relying on the
        suite's fixture being empty — it is not, and a test that passes only
        because of what another file happens to contain is not testing this.
        """
        import src.models.catalog as catalog_module

        monkeypatch.setattr(catalog_module, "get_model_catalog_path",
                            lambda: tmp_path / "nothing-here.json")
        resp = unlocked_client.get("/api/settings/models")
        assert resp.status_code == 200
        assert resp.json()["models"] == []

    def test_a_name_without_a_provider_is_refused_before_anything_is_billed(
        self, unlocked_client, monkeypatch
    ):
        """'gpt-5.2' alone can't be looked up, and saying so costs nothing."""
        import src.models.pricing as pricing_module

        def must_not_run(*args, **kwargs):
            raise AssertionError("the provider was contacted for a name that can't work")

        monkeypatch.setattr(pricing_module, "add_model_to_catalog", must_not_run)
        resp = unlocked_client.post("/api/settings/models", json={
            "provider_model": "gpt-5.2", "professor": "smith",
        })
        assert resp.status_code == 400
        assert "slash" in resp.json()["detail"]

    def test_an_unknown_professor_is_refused(self, unlocked_client):
        resp = unlocked_client.post("/api/settings/models", json={
            "provider_model": "openai/gpt-4o", "professor": "nobody",
        })
        assert resp.status_code == 400

    def test_adding_tests_the_model_and_reports_what_it_found(
        self, unlocked_client, monkeypatch
    ):
        app_module = sys.modules["_pu_webui_app"]
        captured = {}

        def fake_add(provider_model, api_key=None, probe=True):
            captured["provider_model"] = provider_model
            captured["api_key"] = api_key
            return "gpt-5.2", {"input": 1.0, "output": 2.0, "supports_vision": True}

        monkeypatch.setattr("src.models.add_model_to_catalog", fake_add)
        monkeypatch.setattr(app_module, "_capability_summary",
                            lambda m: {"supports_vision": True, "refuses": [],
                                       "prefers": {}, "tested": True})
        monkeypatch.setattr("src.config.get_api_key", lambda netid: ("sk-test", "primary"))

        resp = unlocked_client.post("/api/settings/models", json={
            "provider_model": "openai/gpt-5.2", "professor": "smith",
        })
        assert resp.status_code == 200
        assert captured["provider_model"] == "openai/gpt-5.2"
        # The key has to reach the add, or the model arrives untested — which
        # is the whole failure this section exists to prevent.
        assert captured["api_key"] == "sk-test"
        assert resp.json()["capabilities"]["supports_vision"] is True


class TestTestingAModelAgainFromTheBrowser:
    """Correcting an entry recorded before any testing existed."""

    def _fake_report(self, **kw):
        from src.models.capabilities import CapabilityReport
        return CapabilityReport(**kw)

    def test_a_model_not_in_the_catalog_is_a_404(self, unlocked_client, monkeypatch):
        monkeypatch.setattr("src.models.load_model_catalog",
                            lambda: {"config": {}, "models": {}})
        resp = unlocked_client.post("/api/settings/models/no-such-model/test",
                                    json={"professor": "smith"})
        assert resp.status_code == 404

    def test_a_successful_test_saves_and_reports(
        self, unlocked_client, monkeypatch, a_catalog
    ):
        """A model recorded as text-only by assumption is corrected in place."""
        saved = {}
        monkeypatch.setattr("src.models.save_model_catalog",
                            lambda c: saved.update(c["models"]))
        monkeypatch.setattr("src.config.get_api_key", lambda netid: ("sk-test", "primary"))
        monkeypatch.setattr("src.models.capabilities.probe_model_capabilities",
                            lambda name, client: self._fake_report(
                                findings={"supports_vision": True},
                                settled=["Can read images"]))

        resp = unlocked_client.post("/api/settings/models/old-text-model/test",
                                    json={"professor": "smith"})
        assert resp.status_code == 200
        assert resp.json()["settled"] == ["Can read images"]
        assert saved["old-text-model"]["supports_vision"] is True
        # The price it already had is not lost to a test about capabilities.
        assert saved["old-text-model"]["input"] == 1.0

    def test_a_model_that_cannot_be_reached_changes_nothing(
        self, unlocked_client, monkeypatch, a_catalog
    ):
        """The restraint that matters, carried through to the browser.

        A model that couldn't be tested must not come back recorded as unable
        to read images — that is indistinguishable from having tested it.
        """
        def must_not_save(catalog):
            raise AssertionError("a failed test wrote to the catalog")

        monkeypatch.setattr("src.models.save_model_catalog", must_not_save)
        monkeypatch.setattr("src.config.get_api_key", lambda netid: ("sk-test", "primary"))
        monkeypatch.setattr("src.models.capabilities.probe_model_capabilities",
                            lambda name, client: self._fake_report(
                                reachable=False, unsettled=["Testing stopped early: timed out"]))

        resp = unlocked_client.post("/api/settings/models/gpt-4o/test",
                                    json={"professor": "smith"})
        assert resp.status_code == 502
        assert "could not be reached" in resp.json()["detail"]


class TestAModelThatNoLongerExists:
    """gpt-35-turbo, gpt-35-turbo-16k and gpt-4-32k had all been retired.

    Testing recorded each as "tested, text only" with a date, because every
    probe failed identically and that read as a model refusing everything. The
    browser has to say what is actually true and offer the one useful action.
    """

    def test_it_is_reported_as_gone_not_as_a_failed_test(
        self, unlocked_client, monkeypatch, a_catalog
    ):
        from src.models.capabilities import CapabilityReport

        def must_not_save(catalog):
            raise AssertionError("a retired model was written to the catalog")

        monkeypatch.setattr("src.models.save_model_catalog", must_not_save)
        monkeypatch.setattr("src.config.get_api_key", lambda netid: ("sk-test", "primary"))
        monkeypatch.setattr("src.models.capabilities.probe_model_capabilities",
                            lambda name, client: CapabilityReport(
                                reachable=False, missing=True,
                                unsettled=["There is no such model"]))

        resp = unlocked_client.post("/api/settings/models/old-text-model/test",
                                    json={"professor": "smith"})
        # 410, not 502: nothing is wrong with the request or the connection.
        assert resp.status_code == 410
        assert "no longer exists" in resp.json()["detail"]

    def test_it_can_be_removed(self, unlocked_client, a_catalog):
        assert unlocked_client.delete("/api/settings/models/old-text-model").status_code == 200
        remaining = [m["name"] for m in
                     unlocked_client.get("/api/settings/models").json()["models"]]
        assert remaining == ["gpt-4o"]

    def test_removing_one_that_is_not_there_is_a_404(self, unlocked_client, a_catalog):
        assert unlocked_client.delete("/api/settings/models/never-existed").status_code == 404

    def test_removing_needs_an_unlocked_session(self, client):
        assert client.delete("/api/settings/models/gpt-4o").status_code == 401

    def test_every_model_can_be_removed_from_its_own_row(self):
        """Removal used to appear only after a test came back 410.

        That meant finding out a model was gone cost a billed request, and a
        model somebody simply no longer wanted could not be taken out here at
        all. It is offered on every row now; the confirmation is what stops an
        accidental one, and the entry can be added back in a line.
        """
        page = (WEBUI_SRC / "templates"
                / "settings.html").read_text()
        assert 'data-remove-model="${m.name}">Remove' in page
        assert 'data-remove-model="${m.name}" style="display:none"' not in page
        # Still says so when a test proves the model is gone.
        assert "no longer exists" in page

    def test_the_confirmation_no_longer_claims_the_model_is_gone(self):
        """It could say so when it only appeared after a 410 had proved it."""
        page = (WEBUI_SRC / "templates"
                / "settings.html").read_text()
        confirm = page.split("Remove ${name} from")[1].split("`")[0]
        assert "no longer exists" not in confirm
        assert "Nothing else is affected" in confirm


class TestAddingAModelSaysWhatItIsDoing:
    """Five provider requests in a row is long enough to need narrating.

    The box used to be silent while a model was added and tested — and in the
    chat page it did not add or test anything at all, so the wait landed later,
    in the middle of the first message, with nothing on screen to explain it.
    """

    def _events(self, resp) -> list:
        """The stream, read back as the list of events the browser would see."""
        out = []
        for line in resp.text.splitlines():
            if line.startswith("data: "):
                out.append(json.loads(line[len("data: "):]))
        return out

    def test_it_needs_an_unlocked_session(self, client):
        assert client.post("/api/settings/models/stream", json={
            "provider_model": "openai/gpt-4o", "professor": "smith",
        }).status_code == 401

    def test_a_name_without_a_provider_is_refused_before_anything_is_billed(
        self, unlocked_client, monkeypatch
    ):
        import src.models.pricing as pricing_module

        def must_not_run(*args, **kwargs):
            raise AssertionError("the provider was contacted for a name that can't work")

        monkeypatch.setattr(pricing_module, "add_model_to_catalog", must_not_run)
        resp = unlocked_client.post("/api/settings/models/stream", json={
            "provider_model": "gpt-5.2", "professor": "smith",
        })
        assert resp.status_code == 400
        assert "slash" in resp.json()["detail"]

    def test_every_step_is_named_as_it_starts(self, unlocked_client, monkeypatch):
        """The point of the whole endpoint: the wait is accounted for."""
        app_module = sys.modules["_pu_webui_app"]

        def fake_add(provider_model, api_key=None, probe=True, on_progress=None):
            for label in ("Looking up what it costs", "Checking how to ask it for a length",
                          "Checking how to give it instructions",
                          "Checking which settings it accepts",
                          "Checking whether it can read images"):
                on_progress(label)
            return "gpt-5.2", {"input": 1.0, "output": 2.0}

        monkeypatch.setattr("src.models.add_model_to_catalog", fake_add)
        monkeypatch.setattr(app_module, "_capability_summary",
                            lambda m: {"supports_vision": True, "refuses": [],
                                       "prefers": {}, "tested": True})
        monkeypatch.setattr("src.config.get_api_key", lambda netid: ("sk-test", "primary"))

        events = self._events(unlocked_client.post("/api/settings/models/stream", json={
            "provider_model": "openai/gpt-5.2", "professor": "smith",
        }))
        progress = [e for e in events if e["type"] == "progress"]
        assert [e["label"] for e in progress] == [
            "Looking up what it costs",
            "Checking how to ask it for a length",
            "Checking how to give it instructions",
            "Checking which settings it accepts",
            "Checking whether it can read images",
        ]
        # Numbered, so a bar has something to fill against.
        assert [e["step"] for e in progress] == [1, 2, 3, 4, 5]
        assert all(e["total"] == 5 for e in progress)
        assert events[-1]["type"] == "done"
        assert events[-1]["capabilities"]["supports_vision"] is True

    def test_a_model_that_could_not_be_added_says_so_instead_of_finishing(
        self, unlocked_client, monkeypatch
    ):
        """Almost always the price lookup, on a misspelled name."""
        def fake_add(provider_model, api_key=None, probe=True, on_progress=None):
            on_progress("Looking up what it costs")
            raise RuntimeError("No valid pricing data for 'openai/gpt-nope'")

        monkeypatch.setattr("src.models.add_model_to_catalog", fake_add)
        monkeypatch.setattr("src.config.get_api_key", lambda netid: ("sk-test", "primary"))

        events = self._events(unlocked_client.post("/api/settings/models/stream", json={
            "provider_model": "openai/gpt-nope", "professor": "smith",
        }))
        assert events[-1]["type"] == "error"
        assert "pricing" in events[-1]["message"]
        assert not any(e["type"] == "done" for e in events)

    def test_a_model_already_in_the_catalog_is_not_paid_for_twice(
        self, unlocked_client, monkeypatch, a_catalog
    ):
        """Re-adding repeated every billed request to learn what was on file."""
        def must_not_run(*args, **kwargs):
            raise AssertionError("an already-added model was tested again")

        monkeypatch.setattr("src.models.add_model_to_catalog", must_not_run)
        monkeypatch.setattr("src.config.get_api_key", lambda netid: ("sk-test", "primary"))

        events = self._events(unlocked_client.post("/api/settings/models/stream", json={
            "provider_model": "openai/gpt-4o", "professor": "smith",
        }))
        assert len(events) == 1
        assert events[0]["type"] == "done"
        assert events[0]["already"] is True
        assert events[0]["model"] == "gpt-4o"
