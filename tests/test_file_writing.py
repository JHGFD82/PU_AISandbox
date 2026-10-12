"""Tests for src/file_writing.py: saving a file so nobody sees it half-written."""

from __future__ import annotations

import os
import stat
import sys
import threading

import pytest

from src.file_writing import write_in_one_step


def _scratch_files(folder):
    return [p.name for p in folder.iterdir() if p.name.endswith(".tmp")]


class TestWriteInOneStep:

    def test_it_saves_the_text(self, tmp_path):
        target = tmp_path / "usage.json"
        write_in_one_step(target, '{"total": 1}')
        assert target.read_text(encoding="utf-8") == '{"total": 1}'

    def test_it_makes_the_folder_first(self, tmp_path):
        target = tmp_path / "archives" / "abc123" / "2026-09.json"
        write_in_one_step(target, "{}")
        assert target.read_text() == "{}"

    def test_it_replaces_what_was_there(self, tmp_path):
        target = tmp_path / "settings.toml"
        target.write_text("old")
        write_in_one_step(target, "new")
        assert target.read_text() == "new"
        assert _scratch_files(tmp_path) == []

    def test_text_beyond_plain_english_survives(self, tmp_path):
        """Conversation notes hold whatever language the instructions were in."""
        target = tmp_path / "settings.txt"
        write_in_one_step(target, "漢文を訓読する — Kanbun")
        assert target.read_text(encoding="utf-8") == "漢文を訓読する — Kanbun"


class TestAFailedSaveLeavesNothingBroken:

    def test_the_old_file_is_untouched_and_no_scratch_file_is_left(
            self, tmp_path, monkeypatch):
        target = tmp_path / "model_catalog.json"
        target.write_text("the catalog as it was")

        def refuse(src, dst):
            raise OSError("simulated replace failure")
        monkeypatch.setattr(os, "replace", refuse)

        with pytest.raises(OSError, match="simulated replace failure"):
            write_in_one_step(target, "the new catalog")
        assert target.read_text() == "the catalog as it was"
        assert _scratch_files(tmp_path) == []

    def test_the_first_failure_is_the_one_reported(self, tmp_path, monkeypatch):
        """Failing to tidy up afterwards must not hide why the save failed."""
        def refuse_replace(src, dst):
            raise OSError("simulated replace failure")

        def refuse_unlink(path):
            raise OSError("simulated unlink failure")
        monkeypatch.setattr(os, "replace", refuse_replace)
        monkeypatch.setattr(os, "unlink", refuse_unlink)

        with pytest.raises(OSError, match="simulated replace failure"):
            write_in_one_step(tmp_path / "model_catalog.json", "{}")


class TestTwoWritersNeverShareAScratchFile:
    """With one fixed scratch name, two parallel jobs fill the same scratch
    file at once and the second move finds nothing to move."""

    def test_the_scratch_file_is_named_for_the_writer(self, tmp_path, monkeypatch):
        used = []
        real_replace = os.replace

        def remember(src, dst):
            used.append(str(src))
            return real_replace(src, dst)
        monkeypatch.setattr(os, "replace", remember)

        write_in_one_step(tmp_path / "corrections.json", "{}")
        assert str(os.getpid()) in used[0]
        assert str(threading.get_ident()) in used[0]

    def test_many_writers_at_once_all_succeed(self, tmp_path):
        target = tmp_path / "token_usage_abc123.json"
        errors = []

        def write(n):
            try:
                for _ in range(20):
                    write_in_one_step(target, f'{{"writer": {n}}}')
            except Exception as e:  # pragma: no cover - only reached on failure
                errors.append(e)

        threads = [threading.Thread(target=write, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert target.read_text().startswith('{"writer": ')
        assert _scratch_files(tmp_path) == []


@pytest.mark.skipif(sys.platform == "win32",
                    reason="Windows does not use these permission bits")
class TestWhoCanReadTheFile:

    def test_a_private_file_is_readable_by_its_owner_alone(self, tmp_path):
        """settings.toml holds API keys."""
        target = tmp_path / "settings.toml"
        write_in_one_step(target, "[professors]", private=True)
        assert stat.S_IMODE(target.stat().st_mode) == 0o600

    def test_an_ordinary_file_gets_the_usual_permissions(self, tmp_path):
        """Usage records in a shared folder are read by colleagues."""
        mask = os.umask(0o022)
        try:
            target = tmp_path / "token_usage_abc123.json"
            write_in_one_step(target, "{}")
        finally:
            os.umask(mask)
        assert stat.S_IMODE(target.stat().st_mode) == 0o644
