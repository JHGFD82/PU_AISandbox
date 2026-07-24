"""
Tests for scripts/migrate_usage_records.py — the one-time backfill of the
'source' field onto pre-existing usage records (docs/webui-plugin-plan.md
section 1).

Every test redirects DATA_DIR/BACKUP_DIR to a tmp_path location so the real
repository data/ folder is never touched.
"""

import json
from unittest.mock import patch

import pytest

import scripts.migrate_usage_records as migrate_mod
from scripts.migrate_usage_records import migrate


@pytest.fixture(autouse=True)
def _redirect_data_dir(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(migrate_mod, "DATA_DIR", data_dir)
    monkeypatch.setattr(migrate_mod, "BACKUP_DIR", data_dir / "_pre_migration_backup")
    with patch("scripts.migrate_usage_records.get_source_id", return_value="toms-mac"):
        yield data_dir


class TestMigrateNoFiles:

    def test_no_files_prints_nothing_to_do(self, capsys, _redirect_data_dir):
        migrate()
        out = capsys.readouterr().out
        assert "nothing to migrate" in out

    def test_no_files_creates_no_backup(self, _redirect_data_dir):
        migrate()
        assert not (migrate_mod.BACKUP_DIR).exists()


class TestMigrateBackfillsSource:

    def _write_active_file(self, data_dir, professor, records):
        data = {
            "month": "2026-07",
            "total_usage": {},
            "model_usage": {},
            "daily_usage": {},
            "session_history": records,
        }
        (data_dir / f"token_usage_{professor}.json").write_text(json.dumps(data))

    def test_backfills_missing_source(self, _redirect_data_dir):
        self._write_active_file(_redirect_data_dir, "heller", [
            {"model": "gpt-4o", "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2,
             "timestamp": "2026-07-01T10:00:00", "input_cost": 0.0, "output_cost": 0.0, "total_cost": 0.0},
        ])
        migrate()
        on_disk = json.loads((_redirect_data_dir / "token_usage_heller.json").read_text())
        assert on_disk["session_history"][0]["source"] == "toms-mac"

    def test_leaves_already_tagged_records_untouched(self, _redirect_data_dir):
        self._write_active_file(_redirect_data_dir, "heller", [
            {"model": "gpt-4o", "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2,
             "timestamp": "2026-07-01T10:00:00", "input_cost": 0.0, "output_cost": 0.0,
             "total_cost": 0.0, "source": "already-tagged"},
        ])
        migrate()
        on_disk = json.loads((_redirect_data_dir / "token_usage_heller.json").read_text())
        assert on_disk["session_history"][0]["source"] == "already-tagged"

    def test_updates_archive_files_too(self, _redirect_data_dir):
        archive_dir = _redirect_data_dir / "archives" / "heller"
        archive_dir.mkdir(parents=True)
        (archive_dir / "2026-05.json").write_text(json.dumps({
            "month": "2026-05", "total_usage": {}, "model_usage": {}, "daily_usage": {},
            "session_history": [
                {"model": "gpt-4o", "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2,
                 "timestamp": "2026-05-01T10:00:00", "input_cost": 0.0, "output_cost": 0.0, "total_cost": 0.0},
            ],
        }))
        migrate()
        on_disk = json.loads((archive_dir / "2026-05.json").read_text())
        assert on_disk["session_history"][0]["source"] == "toms-mac"

    def test_prints_summary_counts(self, capsys, _redirect_data_dir):
        self._write_active_file(_redirect_data_dir, "heller", [
            {"model": "gpt-4o", "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2,
             "timestamp": "2026-07-01T10:00:00", "input_cost": 0.0, "output_cost": 0.0, "total_cost": 0.0},
        ])
        migrate()
        out = capsys.readouterr().out
        assert "Updated 1 record(s) across 1 file(s)" in out

    def test_corrupt_file_is_skipped_not_fatal(self, capsys, _redirect_data_dir):
        (_redirect_data_dir / "token_usage_bad.json").write_text("{not valid json")
        migrate()  # should not raise
        out = capsys.readouterr().out
        assert "SKIP (invalid JSON)" in out


class TestMigrateDryRun:

    def _write_active_file(self, data_dir):
        data = {
            "month": "2026-07", "total_usage": {}, "model_usage": {}, "daily_usage": {},
            "session_history": [
                {"model": "gpt-4o", "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2,
                 "timestamp": "2026-07-01T10:00:00", "input_cost": 0.0, "output_cost": 0.0, "total_cost": 0.0},
            ],
        }
        (data_dir / "token_usage_heller.json").write_text(json.dumps(data))

    def test_dry_run_does_not_write_file(self, _redirect_data_dir):
        self._write_active_file(_redirect_data_dir)
        original = (_redirect_data_dir / "token_usage_heller.json").read_text()
        migrate(dry_run=True)
        assert (_redirect_data_dir / "token_usage_heller.json").read_text() == original

    def test_dry_run_creates_no_backup(self, _redirect_data_dir):
        self._write_active_file(_redirect_data_dir)
        migrate(dry_run=True)
        assert not migrate_mod.BACKUP_DIR.exists()

    def test_dry_run_prints_would_update(self, capsys, _redirect_data_dir):
        self._write_active_file(_redirect_data_dir)
        migrate(dry_run=True)
        out = capsys.readouterr().out
        assert "Would update" in out
        assert "Dry run" in out


class TestMigrateBackup:

    def _write_active_file(self, data_dir):
        data = {
            "month": "2026-07", "total_usage": {}, "model_usage": {}, "daily_usage": {},
            "session_history": [
                {"model": "gpt-4o", "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2,
                 "timestamp": "2026-07-01T10:00:00", "input_cost": 0.0, "output_cost": 0.0, "total_cost": 0.0},
            ],
        }
        (data_dir / "token_usage_heller.json").write_text(json.dumps(data))

    def test_backup_copy_matches_original_pre_migration_content(self, _redirect_data_dir):
        self._write_active_file(_redirect_data_dir)
        original = (_redirect_data_dir / "token_usage_heller.json").read_text()
        migrate()
        backup_copy = (migrate_mod.BACKUP_DIR / "token_usage_heller.json").read_text()
        assert backup_copy == original

    def test_second_run_does_not_overwrite_existing_backup(self, capsys, _redirect_data_dir):
        self._write_active_file(_redirect_data_dir)
        migrate()
        capsys.readouterr()

        # Tamper with the backup to prove a second run doesn't touch it
        (migrate_mod.BACKUP_DIR / "token_usage_heller.json").write_text('{"tampered": true}')
        migrate()
        out = capsys.readouterr().out
        assert "Backup already exists" in out
        assert json.loads((migrate_mod.BACKUP_DIR / "token_usage_heller.json").read_text()) == {"tampered": True}
