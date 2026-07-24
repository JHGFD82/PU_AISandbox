"""
Tests for src/env_editor.py:
  - next_professor_id
  - add_professor / remove_professor
  - set_optional_value / unset_optional_value

Every test redirects env_editor.ENV_PATH to a tmp_path location and clears
any PROF_* variables from the real environment first, so nothing here ever
touches the real repo-root .env file or leaks state between tests.
"""

import pytest

import src.env_editor as env_editor


@pytest.fixture(autouse=True)
def _isolate_env(tmp_path, monkeypatch):
    """Point ENV_PATH at a tmp_path file and wipe PROF_*/test env vars first."""
    monkeypatch.setattr(env_editor, "ENV_PATH", tmp_path / ".env")
    for key in list(__import__("os").environ.keys()):
        if key.startswith("PROF_") or key in ("SOME_KEY", "WEBUI_SESSION_SECRET"):
            monkeypatch.delenv(key, raising=False)
    yield


class TestNextProfessorId:

    def test_no_professors_returns_one(self):
        assert env_editor.next_professor_id() == "1"

    def test_returns_one_higher_than_highest(self, monkeypatch):
        monkeypatch.setenv("PROF_1_NAME", "Alice")
        monkeypatch.setenv("PROF_1_KEY", "k1")
        monkeypatch.setenv("PROF_3_NAME", "Bob")
        monkeypatch.setenv("PROF_3_KEY", "k3")
        assert env_editor.next_professor_id() == "4"


class TestAddProfessor:

    def test_writes_name_and_key(self):
        safe_name = env_editor.add_professor("Jeff Heller", "sk-primary")
        assert safe_name == "jeff_heller"
        content = env_editor.ENV_PATH.read_text()
        assert "PROF_1_NAME" in content
        assert "Jeff Heller" in content
        assert "PROF_1_KEY" in content

    def test_updates_current_process_env(self):
        env_editor.add_professor("Jeff Heller", "sk-primary")
        import os
        assert os.environ.get("PROF_1_NAME") == "Jeff Heller"
        assert os.environ.get("PROF_1_KEY") == "sk-primary"

    def test_backup_key_written_when_provided(self):
        env_editor.add_professor("Jeff Heller", "sk-primary", "sk-backup")
        content = env_editor.ENV_PATH.read_text()
        assert "PROF_1_BACKUP_KEY" in content

    def test_backup_key_omitted_when_not_provided(self):
        env_editor.add_professor("Jeff Heller", "sk-primary")
        content = env_editor.ENV_PATH.read_text()
        assert "BACKUP_KEY" not in content

    def test_blank_name_raises(self):
        with pytest.raises(ValueError, match="blank"):
            env_editor.add_professor("   ", "sk-primary")

    def test_blank_primary_key_raises(self):
        with pytest.raises(ValueError, match="blank"):
            env_editor.add_professor("Jeff Heller", "  ")

    def test_duplicate_name_raises(self):
        env_editor.add_professor("Jeff Heller", "sk-primary")
        with pytest.raises(ValueError, match="already configured"):
            env_editor.add_professor("Jeff Heller", "sk-other")

    def test_second_professor_gets_next_id(self):
        env_editor.add_professor("Jeff Heller", "sk-1")
        env_editor.add_professor("Alice Smith", "sk-2")
        content = env_editor.ENV_PATH.read_text()
        assert "PROF_1_NAME" in content
        assert "PROF_2_NAME" in content

    def test_preserves_unrelated_existing_lines(self):
        env_editor.ENV_PATH.write_text("# a comment\nSOME_KEY=some_value\n")
        env_editor.add_professor("Jeff Heller", "sk-primary")
        content = env_editor.ENV_PATH.read_text()
        assert "# a comment" in content
        assert "SOME_KEY" in content
        assert "some_value" in content


class TestRemoveProfessor:

    def test_remove_by_safe_name(self):
        env_editor.add_professor("Jeff Heller", "sk-primary")
        removed = env_editor.remove_professor("jeff_heller")
        assert removed == "Jeff Heller"
        content = env_editor.ENV_PATH.read_text()
        assert "PROF_1_NAME" not in content
        assert "PROF_1_KEY" not in content

    def test_remove_by_display_name_case_insensitive(self):
        env_editor.add_professor("Jeff Heller", "sk-primary")
        removed = env_editor.remove_professor("JEFF HELLER")
        assert removed == "Jeff Heller"

    def test_remove_clears_process_env(self):
        env_editor.add_professor("Jeff Heller", "sk-primary")
        env_editor.remove_professor("jeff_heller")
        import os
        assert "PROF_1_NAME" not in os.environ
        assert "PROF_1_KEY" not in os.environ

    def test_remove_unknown_raises(self):
        with pytest.raises(ValueError, match="No configured professor"):
            env_editor.remove_professor("nobody")

    def test_remove_leaves_other_professors_intact(self):
        env_editor.add_professor("Jeff Heller", "sk-1")
        env_editor.add_professor("Alice Smith", "sk-2")
        env_editor.remove_professor("jeff_heller")
        content = env_editor.ENV_PATH.read_text()
        assert "PROF_2_NAME" in content
        assert "Alice Smith" in content


class TestOptionalValues:

    def test_set_optional_value_writes_and_updates_env(self):
        env_editor.set_optional_value("WEBUI_SESSION_SECRET", "abc123")
        import os
        assert os.environ.get("WEBUI_SESSION_SECRET") == "abc123"
        assert "WEBUI_SESSION_SECRET" in env_editor.ENV_PATH.read_text()

    def test_set_optional_value_blank_raises(self):
        with pytest.raises(ValueError, match="blank"):
            env_editor.set_optional_value("WEBUI_SESSION_SECRET", "   ")

    def test_unset_optional_value_removes_it(self):
        env_editor.set_optional_value("WEBUI_SESSION_SECRET", "abc123")
        env_editor.unset_optional_value("WEBUI_SESSION_SECRET")
        import os
        assert "WEBUI_SESSION_SECRET" not in os.environ
        assert "WEBUI_SESSION_SECRET" not in env_editor.ENV_PATH.read_text()

    def test_unset_missing_value_does_not_raise(self):
        env_editor.unset_optional_value("NEVER_SET_THIS")
