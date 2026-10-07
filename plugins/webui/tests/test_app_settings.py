"""Tests for plugins/webui/src/app.py's settings routes: people, passphrase, values, folders and the shared settings file."""

from __future__ import annotations

import json
import re
import sys
import tomllib

import pytest
from fastapi.testclient import TestClient

import src.settings as core_settings_mod
import src.settings_store as settings_store_mod
from plugins.webui.tests.helpers import (
    WEBUI_SRC,
    _rendered_template,
)

# Every test here runs as two fake professors with no passphrase set; see conftest.py.
pytestmark = pytest.mark.usefixtures("_configured_professors", "_no_passphrase")


# ---------------------------------------------------------------------------
# /settings and /api/settings/* — see plugins/webui/src/templates/settings.html
# ---------------------------------------------------------------------------


class TestSettingsPage:

    def test_page_loads_when_unlocked(self, client, settings_env):
        client.post("/unlock", data={"passphrase": ""})
        resp = client.get("/settings")
        assert resp.status_code == 200

    def test_page_requires_unlock(self, client, settings_env):
        resp = client.get("/settings", follow_redirects=False)
        assert "Unlock" in resp.text or "passphrase" in resp.text.lower()

    def test_api_requires_unlock(self, client, settings_env):
        resp = client.get("/api/settings")
        assert resp.status_code == 401

    def test_no_professors_first_run_order(self, unlocked_client, settings_env):
        data = unlocked_client.get("/api/settings").json()
        assert data["has_professors"] is False
        assert data["order"] == [
            "professors", "webui", "shared", "endpoints", "models",
            "folder", "update",
        ]
        assert data["professors"] == []

    def test_index_redirects_to_settings_with_no_professors(self, unlocked_client, settings_env):
        resp = unlocked_client.get("/", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/settings"


class TestSettingsProfessors:

    def test_add_professor(self, unlocked_client, settings_env):
        resp = unlocked_client.post("/api/settings/professors", json={
            "netid": "jh43", "name": "Jeff Heller", "key": "sk-primary",
        })
        assert resp.status_code == 200
        assert resp.json()["netid"] == "jh43"

        data = unlocked_client.get("/api/settings").json()
        assert data["has_professors"] is True
        assert data["order"] == [
            "update", "folder", "shared", "endpoints", "models", "professors",
            "webui",
        ]
        prof = data["professors"][0]
        assert prof == {
            "netid": "jh43", "name": "Jeff Heller",
            "has_key": True, "has_backup_key": False, "usage": None,
        }

    def test_add_professor_with_backup_key(self, unlocked_client, settings_env):
        unlocked_client.post("/api/settings/professors", json={
            "netid": "jh43", "name": "Jeff Heller", "key": "sk-primary",
            "backup_key": "sk-backup",
        })
        prof = unlocked_client.get("/api/settings").json()["professors"][0]
        assert prof["has_backup_key"] is True

    def test_a_netid_is_required(self, unlocked_client, settings_env):
        """Without one there is no name for their key, their usage file, or their commands."""
        resp = unlocked_client.post("/api/settings/professors", json={
            "name": "Jeff Heller", "key": "sk-primary",
        })
        assert resp.status_code == 422

    def test_a_display_name_in_the_netid_box_is_refused(self, unlocked_client, settings_env):
        """The commonest mistake: 'Jeff Heller' typed where 'jh43' was wanted."""
        resp = unlocked_client.post("/api/settings/professors", json={
            "netid": "Jeff Heller", "name": "Jeff Heller", "key": "sk-primary",
        })
        assert resp.status_code == 400
        assert "netID" in resp.json()["detail"]

    def test_an_email_address_in_the_netid_box_is_refused(self, unlocked_client, settings_env):
        resp = unlocked_client.post("/api/settings/professors", json={
            "netid": "jh43@princeton.edu", "name": "Jeff Heller", "key": "sk-primary",
        })
        assert resp.status_code == 400

    def test_a_netid_typed_in_capitals_is_the_same_person(self, unlocked_client, settings_env):
        resp = unlocked_client.post("/api/settings/professors", json={
            "netid": "JH43", "name": "Jeff Heller", "key": "sk-primary",
        })
        assert resp.status_code == 200
        assert resp.json()["netid"] == "jh43"

    def test_add_professor_blank_key_rejected(self, unlocked_client, settings_env):
        resp = unlocked_client.post("/api/settings/professors", json={"netid": "jh43", "name": "Jeff Heller", "key": "  "})
        assert resp.status_code == 400

    def test_add_duplicate_professor_rejected(self, unlocked_client, settings_env):
        unlocked_client.post("/api/settings/professors", json={"netid": "jh43", "name": "Jeff Heller", "key": "sk-1"})
        resp = unlocked_client.post("/api/settings/professors", json={"netid": "jh43", "name": "Jeff Heller", "key": "sk-2"})
        assert resp.status_code == 400

    def test_remove_professor(self, unlocked_client, settings_env):
        unlocked_client.post("/api/settings/professors", json={"netid": "jh43", "name": "Jeff Heller", "key": "sk-1"})
        resp = unlocked_client.delete("/api/settings/professors/jh43")
        assert resp.status_code == 200
        assert unlocked_client.get("/api/settings").json()["professors"] == []

    def test_remove_unknown_professor_404s(self, unlocked_client, settings_env):
        resp = unlocked_client.delete("/api/settings/professors/nobody")
        assert resp.status_code == 404

    def test_update_professor_key(self, unlocked_client, settings_env):
        unlocked_client.post("/api/settings/professors", json={"netid": "jh43", "name": "Jeff Heller", "key": "sk-old"})
        resp = unlocked_client.post("/api/settings/professors/jh43/key", json={"key": "sk-new"})
        assert resp.status_code == 200
        assert settings_store_mod.get_professors()["jh43"]["key"] == "sk-new"

    def test_update_professor_key_blank_rejected(self, unlocked_client, settings_env):
        unlocked_client.post("/api/settings/professors", json={"netid": "jh43", "name": "Jeff Heller", "key": "sk-old"})
        resp = unlocked_client.post("/api/settings/professors/jh43/key", json={"key": "  "})
        assert resp.status_code == 400

    def test_set_backup_key(self, unlocked_client, settings_env):
        unlocked_client.post("/api/settings/professors", json={"netid": "jh43", "name": "Jeff Heller", "key": "sk-old"})
        resp = unlocked_client.post(
            "/api/settings/professors/jh43/backup-key", json={"backup_key": "sk-backup"}
        )
        assert resp.status_code == 200
        assert unlocked_client.get("/api/settings").json()["professors"][0]["has_backup_key"] is True

    def test_clear_backup_key(self, unlocked_client, settings_env):
        unlocked_client.post("/api/settings/professors", json={
            "netid": "jh43", "name": "Jeff Heller", "key": "sk-old",
            "backup_key": "sk-backup",
        })
        unlocked_client.post("/api/settings/professors/jh43/backup-key", json={"backup_key": None})
        assert unlocked_client.get("/api/settings").json()["professors"][0]["has_backup_key"] is False


class TestSettingsPassphrase:

    def test_set_passphrase(self, unlocked_client, settings_env):
        resp = unlocked_client.post(
            "/api/settings/passphrase", json={"passphrase": "hunter2", "confirm": "hunter2"}
        )
        assert resp.status_code == 200
        assert unlocked_client.get("/api/settings").json()["webui"]["passphrase_configured"] is True

    def test_mismatched_passphrase_rejected(self, unlocked_client, settings_env):
        resp = unlocked_client.post(
            "/api/settings/passphrase", json={"passphrase": "hunter2", "confirm": "different"}
        )
        assert resp.status_code == 400

    def test_empty_passphrase_rejected(self, unlocked_client, settings_env):
        resp = unlocked_client.post("/api/settings/passphrase", json={"passphrase": "", "confirm": ""})
        assert resp.status_code == 400

    def test_stored_value_is_hashed_not_plaintext(self, unlocked_client, settings_env):
        unlocked_client.post("/api/settings/passphrase", json={"passphrase": "hunter2", "confirm": "hunter2"})
        stored = settings_store_mod.get_value("webui.passphrase_hash")
        assert stored != "hunter2"

    def test_clear_passphrase(self, unlocked_client, settings_env):
        unlocked_client.post("/api/settings/passphrase", json={"passphrase": "hunter2", "confirm": "hunter2"})
        resp = unlocked_client.delete("/api/settings/passphrase")
        assert resp.status_code == 200
        assert unlocked_client.get("/api/settings").json()["webui"]["passphrase_configured"] is False


class TestSettingsValues:

    def test_set_shared_settings_path(self, unlocked_client, settings_env):
        resp = unlocked_client.post(
            "/api/settings/values", json={"path": "shared_settings.path", "value": "/tmp/shared.toml"}
        )
        assert resp.status_code == 200
        assert unlocked_client.get("/api/settings").json()["shared"]["shared_settings_path"] == "/tmp/shared.toml"

    def test_generate_session_secret(self, unlocked_client, settings_env):
        resp = unlocked_client.post("/api/settings/values/generate", json={"path": "webui.session_secret"})
        assert resp.status_code == 200
        assert unlocked_client.get("/api/settings").json()["webui"]["session_secret_set"] is True

    def test_unset_value(self, unlocked_client, settings_env):
        unlocked_client.post("/api/settings/values", json={"path": "shared_settings.path", "value": "/tmp/x.toml"})
        resp = unlocked_client.delete("/api/settings/values", params={"path": "shared_settings.path"})
        assert resp.status_code == 200
        assert unlocked_client.get("/api/settings").json()["shared"]["shared_settings_path"] is None

    def test_unregistered_path_rejected(self, unlocked_client, settings_env):
        resp = unlocked_client.post(
            "/api/settings/values", json={"path": "professors.heller.key", "value": "sk-sneaky"}
        )
        assert resp.status_code == 400

    def test_passphrase_hash_path_rejected_via_generic_endpoint(self, unlocked_client, settings_env):
        """webui.passphrase_hash must only ever be set pre-hashed, via /api/settings/passphrase."""
        resp = unlocked_client.post(
            "/api/settings/values", json={"path": "webui.passphrase_hash", "value": "not-a-real-hash"}
        )
        assert resp.status_code == 400

    def test_blank_value_rejected(self, unlocked_client, settings_env):
        resp = unlocked_client.post(
            "/api/settings/values", json={"path": "shared_settings.path", "value": "   "}
        )
        assert resp.status_code == 400

    def test_endpoint_credential_settable(self, unlocked_client, settings_env, monkeypatch):
        fake_endpoints = {"hpc_cluster": {"name": "HPC Cluster", "base_url": "http://x.internal/v1"}}
        # ENDPOINTS is imported by value into both src.settings (real) and app.py's
        # own namespace, so both copies need patching for list_apis() (used inside
        # list_optional_settings()) and _settings_snapshot()'s own loop to agree.
        monkeypatch.setattr(core_settings_mod, "ENDPOINTS", fake_endpoints)
        monkeypatch.setattr(sys.modules["_pu_webui_app"], "ENDPOINTS", fake_endpoints)

        data = unlocked_client.get("/api/settings").json()
        ep = data["shared"]["endpoints"][0]
        assert ep == {
            "name": "hpc_cluster", "display_name": "HPC Cluster", "base_url": "http://x.internal/v1",
            # True unless the endpoint says otherwise — the same default the
            # code that actually connects uses, so the page and the behaviour
            # cannot describe an endpoint differently.
            "openai_compatible": True, "default_model": None, "timeout": 30,
            "address_problem": None,
            "credential_path": "endpoints.hpc_cluster.key", "key_set": False,
        }

        resp = unlocked_client.post(
            "/api/settings/values", json={"path": "endpoints.hpc_cluster.key", "value": "sk-cluster"}
        )
        assert resp.status_code == 200
        ep2 = unlocked_client.get("/api/settings").json()["shared"]["endpoints"][0]
        assert ep2["key_set"] is True


class TestAPersonsUsageFolder:
    """A folder holding somebody's usage belongs to them, so it is set on them."""

    def _add_smith(self, client):
        return client.post("/api/settings/professors", json={
            "netid": "smith", "name": "Prof. Smith", "key": "sk-test",
        })

    def test_setting_a_folder_and_reading_it_back(self, unlocked_client, settings_env):
        self._add_smith(unlocked_client)
        resp = unlocked_client.post("/api/settings/sources", json={
            "netid": "smith", "path": "/tmp/smith-data", "mode": "read-only",
        })
        assert resp.status_code == 200
        people = unlocked_client.get("/api/settings").json()["professors"]
        assert people[0]["usage"] == {"path": "/tmp/smith-data", "mode": "read-only"}

    def test_somebody_with_no_folder_says_so(self, unlocked_client, settings_env):
        self._add_smith(unlocked_client)
        people = unlocked_client.get("/api/settings").json()["professors"]
        assert people[0]["usage"] is None

    def test_a_folder_for_somebody_unknown_is_refused(self, unlocked_client, settings_env):
        """It would otherwise be filed against a person who does not exist."""
        resp = unlocked_client.post("/api/settings/sources", json={
            "netid": "nobody", "path": "/tmp/nobody",
        })
        assert resp.status_code == 400
        assert "added yet" in resp.text

    def test_both_modes_are_accepted(self, unlocked_client, settings_env):
        self._add_smith(unlocked_client)
        for mode in ("read-only", "shared-write"):
            resp = unlocked_client.post("/api/settings/sources", json={
                "netid": "smith", "path": f"/tmp/{mode}", "mode": mode,
            })
            assert resp.status_code == 200, (mode, resp.text)

    def test_an_unrecognised_mode_is_refused(self, unlocked_client, settings_env):
        self._add_smith(unlocked_client)
        resp = unlocked_client.post("/api/settings/sources", json={
            "netid": "smith", "path": "/tmp/smith", "mode": "read-write",
        })
        assert resp.status_code == 400

    def test_the_installation_says_what_it_is_called(self, unlocked_client, settings_env):
        """Shared-write names every file it writes after this installation."""
        assert unlocked_client.get("/api/settings").json()["source_id"]

    def test_clearing_a_folder(self, unlocked_client, settings_env):
        self._add_smith(unlocked_client)
        unlocked_client.post("/api/settings/sources", json={
            "netid": "smith", "path": "/tmp/smith-data",
        })
        resp = unlocked_client.delete("/api/settings/sources", params={"netid": "smith"})
        assert resp.status_code == 200
        assert unlocked_client.get("/api/settings").json()["professors"][0]["usage"] is None

    def test_clearing_a_folder_nobody_set_404s(self, unlocked_client, settings_env):
        self._add_smith(unlocked_client)
        resp = unlocked_client.delete("/api/settings/sources", params={"netid": "smith"})
        assert resp.status_code == 404

    def test_setting_a_folder_says_what_it_moved(self, unlocked_client, settings_env,
                                                 monkeypatch, tmp_path):
        """Work recorded before the folder was set goes with it, and the person
        is told so rather than finding out from a report that got smaller."""
        from src.tracking import relocate, token_tracker

        monkeypatch.setattr(token_tracker, "data_root", lambda: tmp_path / "data")
        monkeypatch.setattr(relocate, "_MOVERS", [])
        (tmp_path / "data").mkdir(exist_ok=True)
        self._add_smith(unlocked_client)
        tracker = token_tracker.TokenTracker("smith")
        tracker.record_usage("gpt-4o", 100, 50, 150)

        resp = unlocked_client.post("/api/settings/sources", json={
            "netid": "smith", "path": str(tmp_path / "dropbox"), "mode": "shared-write",
        })
        assert resp.status_code == 200
        assert "1 call" in resp.json()["moved"]

    def test_an_ordinary_change_with_nothing_to_move_says_nothing(
        self, unlocked_client, settings_env, monkeypatch, tmp_path
    ):
        from src.tracking import relocate, token_tracker

        monkeypatch.setattr(token_tracker, "data_root", lambda: tmp_path / "data")
        monkeypatch.setattr(relocate, "_MOVERS", [])
        (tmp_path / "data").mkdir(exist_ok=True)
        self._add_smith(unlocked_client)
        resp = unlocked_client.post("/api/settings/sources", json={
            "netid": "smith", "path": str(tmp_path / "dropbox"), "mode": "shared-write",
        })
        assert "moved" not in resp.json()

    def test_clearing_brings_the_work_back(self, unlocked_client, settings_env,
                                           monkeypatch, tmp_path):
        from src.tracking import relocate, token_tracker

        monkeypatch.setattr(token_tracker, "data_root", lambda: tmp_path / "data")
        monkeypatch.setattr(relocate, "_MOVERS", [])
        (tmp_path / "data").mkdir(exist_ok=True)
        self._add_smith(unlocked_client)
        unlocked_client.post("/api/settings/sources", json={
            "netid": "smith", "path": str(tmp_path / "dropbox"), "mode": "shared-write",
        })
        token_tracker.TokenTracker("smith").record_usage("gpt-4o", 100, 50, 150)

        resp = unlocked_client.delete("/api/settings/sources", params={"netid": "smith"})
        assert resp.status_code == 200
        assert "1 call" in resp.json()["moved"]
        assert (tmp_path / "data" / "token_usage_smith.json").exists()

    def test_clearing_leaves_the_person_alone(self, unlocked_client, settings_env):
        self._add_smith(unlocked_client)
        unlocked_client.post("/api/settings/sources", json={
            "netid": "smith", "path": "/tmp/smith-data",
        })
        unlocked_client.delete("/api/settings/sources", params={"netid": "smith"})
        people = unlocked_client.get("/api/settings").json()["professors"]
        assert [p["netid"] for p in people] == ["smith"]
        assert people[0]["has_key"] is True


class TestSharedSettingsDraftDownload:
    """The export, for whoever looks after a group's settings but doesn't use a terminal.

    Both the person maintaining the shared file and the members pointing at it
    may work entirely in the browser, so neither should need the command line.
    Members were already covered by the shared-path field; this covers the other
    half.
    """

    def test_it_returns_a_draft(self, unlocked_client):
        r = unlocked_client.get("/api/settings/shared-draft")
        assert r.status_code == 200
        assert "shared_settings" in r.text
        assert "webui serve" in r.text

    def test_it_downloads_under_the_documented_name(self, unlocked_client):
        """Same name the CLI writes, so the docs describe one thing."""
        r = unlocked_client.get("/api/settings/shared-draft")
        assert 'filename="shared-settings.toml"' in r.headers["content-disposition"]

    def test_the_draft_is_inert(self, unlocked_client):
        """Everything commented, so placing it unedited changes nothing."""
        import tomllib
        r = unlocked_client.get("/api/settings/shared-draft")
        assert tomllib.loads(r.text) == {}

    def test_it_is_behind_the_unlock_gate(self, client):
        """It lists this installation's whole settings surface."""
        r = client.get("/api/settings/shared-draft", follow_redirects=False)
        assert r.status_code in (302, 303, 401, 403)

    def test_no_shared_settings_file_is_left_behind(self, unlocked_client, tmp_path, monkeypatch):
        """The sandbox never writes a shared settings file; this hands one over.

        Checked for the draft specifically rather than an empty folder — other
        parts of the app create their own directories under here, and this is
        about what the download does, not what else exists.
        """
        from src import paths as paths_mod
        monkeypatch.setattr(paths_mod, "extras_root", lambda: tmp_path)
        unlocked_client.get("/api/settings/shared-draft")
        assert not (tmp_path / "shared-settings.toml").exists()
        assert list(tmp_path.glob("*.toml")) == []


class TestGuidedSharedSettingsEditor:
    """Choosing a group's settings from a list, rather than editing TOML by hand.

    The plain download works but hands someone a hundred commented lines to read.
    This shows every setting with what it does, what it is set to, and which ones
    appeared since their file was written.
    """

    def test_the_page_is_served(self, unlocked_client):
        r = unlocked_client.get("/shared-settings")
        assert r.status_code == 200
        assert "Shared settings" in r.text

    def test_the_page_is_behind_the_unlock_gate(self, client):
        r = client.get("/shared-settings")
        assert "unlock" in r.text.lower() or r.status_code in (302, 303, 401, 403)

    def test_the_inventory_lists_sections_and_settings(self, unlocked_client):
        data = unlocked_client.get("/api/settings/shared-inventory").json()
        assert data["sections"], "no settings offered at all"
        first = data["sections"][0]
        assert {"section", "sources", "settings"} <= set(first)
        assert {"key", "value", "default", "explanation", "chosen", "new"} <= set(
            first["settings"][0]
        )

    def test_the_inventory_says_where_each_section_comes_from(self, unlocked_client):
        data = unlocked_client.get("/api/settings/shared-inventory").json()
        assert all(s["sources"] for s in data["sections"])

    def test_with_no_shared_file_nothing_is_chosen_or_new(self, unlocked_client, monkeypatch):
        from src import settings_store as store_mod
        monkeypatch.setattr(store_mod, "get_shared_settings_path", lambda: None)
        data = unlocked_client.get("/api/settings/shared-inventory").json()
        every = [s for sec in data["sections"] for s in sec["settings"]]
        assert not any(s["chosen"] for s in every)
        assert not any(s["new"] for s in every)
        assert data["existing_path"] is None

    def test_an_existing_file_marks_what_it_decides_and_what_is_new(
        self, unlocked_client, tmp_path, monkeypatch,
    ):
        from src import settings_store as store_mod
        shared = tmp_path / "lab.toml"
        shared.write_text("[retry]\nmax_retries = 3\n")
        monkeypatch.setattr(store_mod, "get_shared_settings_path", lambda: shared)
        data = unlocked_client.get("/api/settings/shared-inventory").json()
        retry = next(s for s in data["sections"] if s["section"] == "retry")
        decided = next(s for s in retry["settings"] if s["key"] == "max_retries")
        assert decided["chosen"] is True
        assert decided["value"] == "3", "should show the group's value, not the shipped one"
        assert decided["new"] is False
        # Both values have to survive: unticking a setting shows what it falls
        # back to, and it can only show that if the shipped value came along.
        assert decided["default"] != "3", "the shipped value was lost"
        assert decided["default"], "no shipped value to fall back to"
        assert any(s["new"] for s in retry["settings"]), "the rest of the section is new"

    def test_a_setting_nobody_has_decided_reports_the_same_pair(self, unlocked_client, monkeypatch):
        """With no group file, what a setting is and what it falls back to are one thing."""
        from src import settings_store as store_mod

        monkeypatch.setattr(store_mod, "get_shared_settings_path", lambda: None)
        data = unlocked_client.get("/api/settings/shared-inventory").json()
        every = [s for sec in data["sections"] for s in sec["settings"]]
        assert all(s["value"] == s["default"] for s in every)

    def test_building_from_choices_returns_a_downloadable_file(self, unlocked_client):
        r = unlocked_client.post(
            "/api/settings/shared-draft", json={"chosen": {"retry": {"max_retries": "5"}}}
        )
        assert r.status_code == 200
        assert 'filename="shared-settings.toml"' in r.headers["content-disposition"]

    def test_only_the_ticked_settings_are_live(self, unlocked_client):
        import tomllib
        r = unlocked_client.post(
            "/api/settings/shared-draft", json={"chosen": {"retry": {"max_retries": "5"}}}
        )
        parsed = tomllib.loads(r.text)
        assert parsed == {"retry": {"max_retries": 5}}, "nothing else should be set"

    def test_ticking_nothing_gives_a_file_that_changes_nothing(self, unlocked_client):
        import tomllib
        r = unlocked_client.post("/api/settings/shared-draft", json={"chosen": {}})
        assert tomllib.loads(r.text) == {}

    def test_nothing_is_marked_new_in_a_file_built_from_choices(self, unlocked_client):
        """Every setting was just looked at, so anything unticked was left alone."""
        r = unlocked_client.post(
            "/api/settings/shared-draft", json={"chosen": {"retry": {"max_retries": "5"}}}
        )
        assert "NEW:" not in r.text

    def test_a_value_toml_cannot_express_is_refused_with_help(self, unlocked_client):
        """Better a rejected edit than a file that will not parse for the group."""
        r = unlocked_client.post(
            "/api/settings/shared-draft",
            json={"chosen": {"prompt": {"default_system_prompt": "no quotes here"}}},
        )
        assert r.status_code == 400
        assert "quotation marks" in r.text

    def test_a_list_value_survives_the_round_trip(self, unlocked_client):
        import tomllib
        r = unlocked_client.post(
            "/api/settings/shared-draft",
            json={"chosen": {"ocr": {"models": '["gpt-4o", "gpt-4o-mini"]'}}},
        )
        assert tomllib.loads(r.text)["ocr"]["models"] == ["gpt-4o", "gpt-4o-mini"]

    def test_nothing_is_written_to_disk(self, unlocked_client, tmp_path, monkeypatch):
        from src import paths as paths_mod
        monkeypatch.setattr(paths_mod, "extras_root", lambda: tmp_path)
        unlocked_client.post("/api/settings/shared-draft", json={"chosen": {}})
        assert list(tmp_path.glob("*.toml")) == []


class TestBrowseButton:
    """/api/pick-path — the "Browse…" button behind every path box.

    A browser hands a page a file's contents and never its location, which
    is why typing paths by hand was the only option before this. It works
    because the server is on the same computer as the browser, so it can
    open that computer's own chooser. Nothing here opens a real window.
    """

    def test_the_settings_page_is_told_whether_to_draw_the_button(
        self, unlocked_client, settings_env, monkeypatch
    ):
        app_module = sys.modules["_pu_webui_app"]
        monkeypatch.setattr(app_module.file_picker, "available", lambda: False)
        assert unlocked_client.get("/api/settings").json()["can_browse"] is False
        monkeypatch.setattr(app_module.file_picker, "available", lambda: True)
        assert unlocked_client.get("/api/settings").json()["can_browse"] is True

    def test_choosing_a_folder_hands_back_its_real_path(
        self, unlocked_client, monkeypatch, tmp_path
    ):
        app_module = sys.modules["_pu_webui_app"]
        monkeypatch.setattr(app_module.file_picker, "choose", lambda **kw: tmp_path / "picked")
        resp = unlocked_client.post("/api/pick-path", json={"kind": "folder"})
        assert resp.status_code == 200
        assert resp.json() == {"path": str(tmp_path / "picked"), "cancelled": False}

    def test_closing_the_window_is_an_answer_not_an_error(self, unlocked_client, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]
        monkeypatch.setattr(app_module.file_picker, "choose", lambda **kw: None)
        resp = unlocked_client.post("/api/pick-path", json={"kind": "folder"})
        assert resp.status_code == 200
        assert resp.json() == {"path": None, "cancelled": True}

    def test_what_was_typed_is_where_the_chooser_opens(self, unlocked_client, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]
        asked = {}
        monkeypatch.setattr(
            app_module.file_picker, "choose",
            lambda **kw: asked.update(kw) or None,
        )
        unlocked_client.post(
            "/api/pick-path",
            json={"kind": "file", "start": "/Users/x/shared", "prompt": "Pick the file"},
        )
        assert asked == {"kind": "file", "start": "/Users/x/shared", "prompt": "Pick the file"}

    def test_a_computer_with_no_chooser_says_so(self, unlocked_client, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]

        def unavailable(**kw):
            raise app_module.file_picker.PickerUnavailable("no chooser here")

        monkeypatch.setattr(app_module.file_picker, "choose", unavailable)
        resp = unlocked_client.post("/api/pick-path", json={"kind": "folder"})
        assert resp.status_code == 503

    def test_asking_for_something_that_is_not_a_file_or_folder(self, unlocked_client, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]

        def refuse(**kw):
            raise ValueError("kind must be 'folder' or 'file'")

        monkeypatch.setattr(app_module.file_picker, "choose", refuse)
        resp = unlocked_client.post("/api/pick-path", json={"kind": "printer"})
        assert resp.status_code == 400

    def test_a_locked_browser_cannot_open_a_window(self, client, monkeypatch):
        app_module = sys.modules["_pu_webui_app"]
        opened = []
        monkeypatch.setattr(app_module.file_picker, "choose", lambda **kw: opened.append(kw))
        resp = client.post("/api/pick-path", json={"kind": "folder"})
        assert resp.status_code == 401
        assert opened == []

    def test_a_browser_on_another_computer_is_refused(self, monkeypatch, tmp_path):
        # The window would open on the screen of whoever runs the sandbox,
        # and hand back a folder from a disk the clicker has never seen.
        app_module = sys.modules["_pu_webui_app"]
        conversation = sys.modules["_pu_webui_conversation"]
        jobs = sys.modules["_pu_webui_jobs"]
        monkeypatch.setattr(conversation, "CONVERSATIONS_DIR", tmp_path / "conversations")
        monkeypatch.setattr(jobs, "_CONVERSATIONS_DIR", tmp_path / "conversations")
        opened = []
        monkeypatch.setattr(app_module.file_picker, "choose", lambda **kw: opened.append(kw))

        elsewhere = TestClient(app_module.create_app(), client=("10.0.0.5", 50000))
        elsewhere.post("/unlock", data={"passphrase": ""})
        resp = elsewhere.post("/api/pick-path", json={"kind": "folder"})
        assert resp.status_code == 403
        assert opened == []


class TestTheEndpointListDescribesEndpointsTruthfully:
    def test_an_endpoint_that_says_nothing_is_shown_as_usable(self, unlocked_client, monkeypatch):
        """The page had its own default, opposite to the one that decides."""
        import sys

        app_module = sys.modules["_pu_webui_app"]
        monkeypatch.setattr(app_module, "ENDPOINTS", {"quiet": {"base_url": "https://x/v1"}})
        shown = unlocked_client.get("/api/settings/endpoints?professor=heller")
        if shown.status_code != 200:  # the list lives on the settings payload
            shown = unlocked_client.get("/api/settings")
        assert shown.status_code == 200
        assert "quiet" in shown.text
        assert "cannot use it" not in shown.text

    def test_the_page_only_remarks_on_one_it_cannot_use(self):

        page = (WEBUI_SRC / "templates" / "settings.html").read_text()
        assert "marked as not OpenAI-compatible" in page
        assert '" · OpenAI-compatible"' not in page


class TestTheSettingsPageSaysThingsOnce:
    def _source(self):

        return (WEBUI_SRC / "templates" / "settings.html").read_text()

    def test_the_page_carries_no_second_heading_inside_the_modal(self):
        """The modal already says "Settings" and already has a way out; a
        heading, a close and a Quit button under them are three ways of saying
        what has been said."""
        source = self._source()
        assert 'document.getElementById("topbar").hidden = embeddedInModal;' in source

    def test_but_keeps_it_when_opened_on_its_own(self):
        """Then the bar is the only heading, and the only way to lock."""
        source = self._source()
        assert 'id="quit-btn"' in source
        assert "hidden = embeddedInModal" in source
        # The bar specifically. Other things on this page are hidden and shown
        # by their own logic — an empty-catalog note, for one — and reading
        # any of those as this rule made the check fail for the wrong reason.
        assert not re.search(r'getElementById\("topbar"\)\.hidden\s*=\s*(true|false)', source)

    def test_shared_settings_and_endpoints_are_separate(self):
        """They are different things: one is defaults a group follows, the
        other is another AI service to call."""
        source = self._source()
        assert 'data-section="shared"' in source
        assert 'data-section="endpoints"' in source
        assert "Shared settings &amp; alternate endpoints" not in source

    def test_both_appear_in_the_order_the_server_gives(self, unlocked_client):
        order = unlocked_client.get("/api/settings").json()["order"]
        assert "shared" in order and "endpoints" in order
        source = self._source()
        for key in order:
            assert f'data-section="{key}"' in source, f"{key} is ordered but not on the page"


class TestWhatEachConversationKeeps:
    """The two boxes deciding whether a conversation's folder holds documents.

    Both were reachable only by editing a file by hand before this — one of them
    only through the shared settings editor, which writes a whole group's file,
    and the other did not exist at all.
    """

    def test_it_says_what_is_set_now(self, unlocked_client):
        body = unlocked_client.get("/api/settings/conversation-folder").json()
        assert set(body) == {"keep_supplied_documents", "keep_job_outputs"}
        assert all(isinstance(v, bool) for v in body.values())

    def test_reading_requires_unlock(self, client):
        assert client.get("/api/settings/conversation-folder").status_code == 401

    def test_changing_requires_unlock(self, client):
        resp = client.post("/api/settings/conversation-folder",
                           json={"keep_job_outputs": False})
        assert resp.status_code == 401

    def test_ticking_a_box_writes_it_to_the_persons_preferences(
        self, unlocked_client, preference_file
    ):
        resp = unlocked_client.post("/api/settings/conversation-folder",
                                    json={"keep_supplied_documents": True})
        assert resp.status_code == 200
        assert tomllib.loads(preference_file.read_text())["webui"]["keep_supplied_documents"] is True

    def test_a_box_not_sent_is_not_touched(self, unlocked_client, preference_file):
        """A page saving one box must not decide the other one is off."""
        preference_file.write_text("[webui]\nkeep_job_outputs = false\n")
        unlocked_client.post("/api/settings/conversation-folder",
                             json={"keep_supplied_documents": True})
        assert tomllib.loads(preference_file.read_text())["webui"]["keep_job_outputs"] is False

    def test_it_answers_with_what_the_file_now_says(self, unlocked_client, preference_file):
        """Not with what was asked for — a shared file may have the last word."""
        body = unlocked_client.post("/api/settings/conversation-folder",
                                    json={"keep_job_outputs": False}).json()
        assert body["keep_job_outputs"] is False

    def test_the_setting_is_read_again_rather_than_at_startup(self, preference_file):
        """Ticking a box and being told to restart would be no use to anyone."""
        from src.settings import is_on
        preference_file.write_text("[webui]\n")
        import src.plugin_preferences as pp
        # The webui plugin reads its own settings.toml plus preferences.toml.
        assert is_on("keep_job_outputs", True) is True
        pp.set_live(preference_file, "webui", "keep_job_outputs", "false")
        assert is_on("keep_job_outputs", True) is False


class TestTheWebFirstRunExplainsItself:
    """Walking in with nothing configured, and being told what is missing.

    A new installation has two things to do before it can be used: somebody to
    bill, and a model to send to. Neither can be shipped — one is a private
    credential, the other depends on the institution's own AI sandbox — so what
    matters is that both are named before anything is typed.
    """

    def _no_models(self, monkeypatch, tmp_path):
        import src.models.catalog as catalog_module
        monkeypatch.setattr(catalog_module, "get_model_catalog_path",
                            lambda: tmp_path / "not-here.json")

    def test_with_no_professor_the_chat_page_is_not_offered(
        self, unlocked_client, settings_env
    ):
        resp = unlocked_client.get("/", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/settings"

    def test_with_no_models_the_chat_page_is_not_offered_either(
        self, unlocked_client, monkeypatch, tmp_path, settings_env
    ):
        """The gap this closes: a chat window that looks ready and fails on the
        first message, because there is nothing to send it to."""
        self._no_models(monkeypatch, tmp_path)
        settings_store_mod.add_professor("jh43", "Jeff Heller", "k")
        resp = unlocked_client.get("/", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/settings"

    def test_the_settings_page_says_which_of_the_two_is_outstanding(
        self, unlocked_client, monkeypatch, tmp_path, settings_env
    ):
        self._no_models(monkeypatch, tmp_path)
        body = unlocked_client.get("/api/settings").json()
        assert body["has_professors"] is False
        assert body["has_models"] is False
        settings_store_mod.add_professor("jh43", "Jeff Heller", "k")
        body = unlocked_client.get("/api/settings").json()
        assert body["has_professors"] is True
        assert body["has_models"] is False

    def test_the_page_opens_on_whichever_step_is_left(self):
        """Adding the professor moves it on to Models by itself."""
        page = _rendered_template("settings.html")
        fn = page.split("function openingTab")[1].split("\n}")[0]
        assert 'if (!data.has_professors) return "system";' in fn
        assert 'if (!data.has_models) return "models";' in fn
        # And once neither is outstanding, it goes back to remembering.
        assert 'localStorage.getItem("settings-tab")' in fn

    def test_the_page_says_where_to_find_out_what_to_add(self):
        """Somewhere on the panel, not only when the list is empty.

        It used to be in the empty-state note alone, so it disappeared the
        moment somebody added their first model — which is before most of the
        adding is done.
        """
        card = _rendered_template("settings.html").split('data-section="models"')[1]
        card = card.split('id="section-shared"')[0]
        words = " ".join(card.split())
        assert "AI Sandbox" in words
        # A link to the list of models, so nobody has to go looking for it.
        assert "href=" in card and "princeton" in card.lower()

    def test_that_note_is_shown_only_while_there_are_none(self):
        page = _rendered_template("settings.html")
        fn = page.split("async function loadModels")[1].split("\n}")[0]
        assert "note.hidden = data.models.length > 0;" in fn

    def test_a_catalog_that_would_not_load_is_a_different_thing(self):
        """Not the same as having none, and it must not read as advice."""
        page = _rendered_template("settings.html")
        fn = page.split("async function loadModels")[1].split("\n}")[0]
        failure = fn.split("catch")[1]
        assert "note.hidden = true;" in failure
        assert "Could not read the model catalog" in failure


class TestAddingTheFirstModel:
    """The one thing a new installation must be able to do.

    Adding a model reads the catalog, puts the entry in, and saves it. While
    an empty catalog refused to be read, that first step raised — so the very
    situation the Models panel exists for was the one it could not handle, and
    the person was told "there are no models set up yet" while trying to set
    one up.
    """

    def _empty_catalog(self, monkeypatch, tmp_path):
        import src.models.catalog as catalog_module
        path = tmp_path / "model_catalog.json"
        path.write_text(json.dumps(
            {"config": {"pricing_unit": 1000000, "provider_map": {}}, "models": {}}))
        monkeypatch.setattr(catalog_module, "get_model_catalog_path", lambda: path)
        monkeypatch.setattr(catalog_module, "_catalog_cache", None)
        return path

    def test_the_models_panel_loads_with_none(
        self, unlocked_client, monkeypatch, tmp_path
    ):
        self._empty_catalog(monkeypatch, tmp_path)
        resp = unlocked_client.get("/api/settings/models")
        assert resp.status_code == 200
        assert resp.json()["models"] == []

    def test_the_settings_page_still_answers(
        self, unlocked_client, monkeypatch, tmp_path
    ):
        """It reports has_models, which means reading a catalog with none."""
        self._empty_catalog(monkeypatch, tmp_path)
        resp = unlocked_client.get("/api/settings")
        assert resp.status_code == 200
        assert resp.json()["has_models"] is False

    def test_adding_one_gets_past_reading_the_catalog(
        self, unlocked_client, monkeypatch, tmp_path, settings_env
    ):
        """Not a test of the provider call — of the step that used to raise first.

        Whatever the request to the provider does, it has to be *reached*.
        While an empty catalog refused to be read, it never was.
        """
        self._empty_catalog(monkeypatch, tmp_path)
        settings_store_mod.add_professor("jh43", "Jeff Heller", "a-key")
        reached = {}
        import src.models as models_module

        def add(name, *a, **kw):
            reached["name"] = name
            raise ValueError("stop here — the point is that we got this far")

        monkeypatch.setattr(models_module, "add_model_to_catalog", add)
        unlocked_client.post("/api/settings/models",
                             json={"provider_model": "openai/gpt-4o", "professor": "jh43"})
        assert reached.get("name") == "openai/gpt-4o", (
            "the catalog read raised before the model could be looked up"
        )
