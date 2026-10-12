"""Tests for hostnames.py: the sandbox answers only to this computer's own names."""

import sys

import pytest
from fastapi.testclient import TestClient

hostnames = sys.modules["_pu_webui_hostnames"]
app_module = sys.modules["_pu_webui_app"]
setup_web = sys.modules["_pu_webui_setup_web"]

pytestmark = pytest.mark.usefixtures("_configured_professors", "_no_passphrase")


class TestIsLoopback:
    @pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "localhost", "LOCALHOST",
                                      "::1", "[::1]", " 127.0.0.1 "])
    def test_this_computers_names(self, host):
        assert hostnames.is_loopback(host)

    @pytest.mark.parametrize("host", ["0.0.0.0", "10.0.0.5", "attacker.example",
                                      "localhost.attacker.example", "", "::"])
    def test_anything_else(self, host):
        assert not hostnames.is_loopback(host)


class TestNameAskedFor:
    @pytest.mark.parametrize("header, name", [
        ("127.0.0.1:8000", "127.0.0.1"),
        ("localhost", "localhost"),
        ("[::1]:8000", "::1"),
        ("Attacker.Example:8000", "attacker.example"),
        ("", None),
        ("[::1", None),
    ])
    def test_the_port_is_left_off(self, header, name):
        assert hostnames.name_asked_for(header) == name


class TestTheSandbox:
    """A page on another website, pointed at 127.0.0.1, asks for its own name."""

    def test_a_foreign_name_cannot_unlock(self, client):
        response = client.post("/unlock", headers={"Host": "attacker.example:8000"},
                               follow_redirects=False)
        assert response.status_code == 400
        assert "127.0.0.1" in response.text

    def test_a_foreign_name_cannot_reach_anything_after_unlocking(self, client):
        # The person's own tab unlocks; the other site's page then shares the
        # same browser, but not the same name.
        client.post("/unlock", follow_redirects=False)
        response = client.get("/api/professors", headers={"Host": "attacker.example:8000"})
        assert response.status_code == 400

    @pytest.mark.parametrize("host", ["127.0.0.1:8000", "localhost:8000", "[::1]:8000"])
    def test_this_computers_own_names_are_answered(self, client, host):
        client.post("/unlock", headers={"Host": host}, follow_redirects=False)
        response = client.get("/api/professors", headers={"Host": host})
        assert response.status_code == 200

    def test_listening_for_other_computers_answers_any_name(self):
        # Only allowed with a passphrase set, which is what protects it then.
        app = app_module.create_app(host="0.0.0.0")
        response = TestClient(app, base_url="http://sandbox.example:8000").get("/")
        assert response.status_code == 200


def test_the_setup_page_refuses_a_foreign_name():
    app = setup_web.create_setup_app(lambda folder: None)
    assert TestClient(app, base_url="http://attacker.example:8000").get("/").status_code == 400
    assert TestClient(app, base_url="http://127.0.0.1:8000").get("/").status_code == 200
