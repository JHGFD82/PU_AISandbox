"""Tests for src/tracking/relocate.py — moving one person's work when the
folder it belongs in changes.

The thing being protected here is that changing one setting never costs
somebody what was recorded before it. Every test either counts what survives a
move or names what was deliberately left where it was.
"""

import json
from datetime import datetime

import pytest

from src import settings_store
from src.settings_store import ExternalSource
from src.tracking import relocate, token_tracker
from src.tracking.relocate import Moved, move_a_persons_work, work_folder

THIS_MONTH = datetime.now().strftime("%Y-%m")


def a_call(tokens=100, cost=0.01, when=None, model="gpt-4o"):
    """One call record, shaped the way the sandbox writes them."""
    return {
        "model": model, "prompt_tokens": tokens // 2, "completion_tokens": tokens // 2,
        "total_tokens": tokens, "timestamp": when or f"{THIS_MONTH}-01T10:00:00",
        "input_cost": cost / 2, "output_cost": cost / 2, "total_cost": cost,
        "source": "a-machine", "endpoint": "",
    }


def a_month(records, month=THIS_MONTH):
    """A month summary holding those calls, as an archive file holds one."""
    return token_tracker.fold_usage_records(records, month)


@pytest.fixture(autouse=True)
def here(tmp_path, monkeypatch):
    """This installation's own files folder, somewhere disposable."""
    local = tmp_path / "local"
    local.mkdir()
    monkeypatch.setattr(token_tracker, "data_root", lambda: local)
    monkeypatch.setattr(settings_store, "SETTINGS_PATH", tmp_path / "settings.toml")
    # Only what a test registers, so one test's mover cannot reach another.
    monkeypatch.setattr(relocate, "_MOVERS", [])
    return local


@pytest.fixture
def shared(tmp_path):
    """A folder somebody's work is written into."""
    return ExternalSource(label="Theirs", path=str(tmp_path / "dropbox"),
                          mode="shared-write", professor="smith")


def tokens_here(netid="smith"):
    """What this installation's own folder says they have spent."""
    tree = token_tracker.load_usage_tree(token_tracker.data_root())
    return sum(m["total_usage"]["total_tokens"] for m in tree.get(netid, {}).values())


def tokens_in(source, netid="smith"):
    """What a shared folder says they have spent."""
    tree = token_tracker.load_usage_tree(source.resolved_path(), netid)
    return sum(m["total_usage"]["total_tokens"] for m in tree.get(netid, {}).values())


def put_locally(here, netid="smith", records=(), archives=()):
    """Write usage into this installation's own folder, as it keeps it."""
    if records:
        (here / f"token_usage_{netid}.json").write_text(
            json.dumps(a_month(list(records))))
    for month, recs in archives:
        d = here / "archives" / netid
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{month}.json").write_text(json.dumps(a_month(list(recs), month)))


def put_in_shared(source, records=(), archives=()):
    """Write usage into a shared folder, as it keeps it."""
    root = source.resolved_path()
    for i, record in enumerate(records):
        d = root / "calls" / THIS_MONTH
        d.mkdir(parents=True, exist_ok=True)
        (d / f"call{i}.json").write_text(json.dumps(record))
    for month, recs in archives:
        d = root / "archives"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{month}.json").write_text(json.dumps(a_month(list(recs), month)))


# ---------------------------------------------------------------------------
# When nothing needs to happen
# ---------------------------------------------------------------------------

class TestWhenTheWorkFolderHasNotChanged:

    def test_no_folder_before_or_after(self, here):
        put_locally(here, records=[a_call()])
        assert not move_a_persons_work("smith", None, None)
        assert tokens_here() == 100

    def test_a_folder_only_being_watched_is_not_a_work_folder(self, tmp_path):
        """Read only means their work stays here, so setting one moves nothing."""
        watched = ExternalSource(label="x", path=str(tmp_path / "d"),
                                 mode="read-only", professor="smith")
        assert work_folder(watched) is None
        assert not move_a_persons_work("smith", None, watched)

    def test_changing_only_the_label_moves_nothing(self, shared, here):
        put_locally(here, records=[a_call()])
        renamed = ExternalSource(label="A new name", path=shared.path,
                                 mode="shared-write", professor="smith")
        move_a_persons_work("smith", shared, shared)
        assert not move_a_persons_work("smith", shared, renamed)

    def test_an_empty_folder_moves_nothing(self, shared):
        assert not move_a_persons_work("smith", None, shared)


# ---------------------------------------------------------------------------
# Out to a shared folder, and back
# ---------------------------------------------------------------------------

class TestGivingSomebodyASharedFolder:

    def test_this_months_calls_go_with_them(self, here, shared):
        put_locally(here, records=[a_call(100), a_call(200)])
        moved = move_a_persons_work("smith", None, shared)
        assert moved.counts["calls"] == 2
        assert tokens_in(shared) == 300

    def test_and_are_no_longer_here(self, here, shared):
        put_locally(here, records=[a_call(100)])
        move_a_persons_work("smith", None, shared)
        assert tokens_here() == 0
        assert not (here / "token_usage_smith.json").exists()

    def test_finished_months_go_too(self, here, shared):
        put_locally(here, archives=[("2026-05", [a_call(50)])])
        moved = move_a_persons_work("smith", None, shared)
        assert moved.counts["finished months"] == 1
        assert (shared.resolved_path() / "archives" / "2026-05.json").exists()

    def test_nothing_in_the_shared_folder_is_filed_under_a_netid(self, here, shared):
        put_locally(here, records=[a_call()], archives=[("2026-05", [a_call()])])
        move_a_persons_work("smith", None, shared)
        assert not any(p.name == "smith" for p in shared.resolved_path().rglob("*"))

    def test_each_call_becomes_a_file_of_its_own(self, here, shared):
        """That is what makes the folder safe for several computers at once."""
        put_locally(here, records=[a_call(), a_call(), a_call()])
        move_a_persons_work("smith", None, shared)
        written = list((shared.resolved_path() / "calls" / THIS_MONTH).glob("*.json"))
        assert len(written) == 3
        assert len({p.name for p in written}) == 3


class TestTakingTheSharedFolderAway:

    def test_this_months_calls_come_home(self, shared):
        put_in_shared(shared, records=[a_call(100), a_call(200)])
        moved = move_a_persons_work("smith", shared, None)
        assert moved.counts["calls"] == 2
        assert tokens_here() == 300

    def test_and_are_no_longer_there(self, shared):
        put_in_shared(shared, records=[a_call(100)])
        move_a_persons_work("smith", shared, None)
        assert tokens_in(shared) == 0

    def test_they_land_in_the_file_this_month_is_kept_in(self, here, shared):
        put_in_shared(shared, records=[a_call(100)])
        move_a_persons_work("smith", shared, None)
        assert (here / "token_usage_smith.json").exists()

    def test_a_month_that_ended_while_away_is_filed_as_finished(self, here, shared):
        """It is over, so it belongs with the finished months rather than
        sitting in the file for the month under way."""
        old = shared.resolved_path() / "calls" / "2020-01"
        old.mkdir(parents=True)
        (old / "a.json").write_text(json.dumps(a_call(70, when="2020-01-05T09:00:00")))
        move_a_persons_work("smith", shared, None)
        assert (here / "archives" / "smith" / "2020-01.json").exists()
        # And not in the file for the month under way, which would report a
        # five-year-old call as this month's spending.
        assert not (here / "token_usage_smith.json").exists()

    def test_finished_months_are_filed_under_them_again(self, here, shared):
        put_in_shared(shared, archives=[("2026-05", [a_call(50)])])
        move_a_persons_work("smith", shared, None)
        assert (here / "archives" / "smith" / "2026-05.json").exists()


class TestMovingFromOneSharedFolderToAnother:

    def test_everything_goes_across(self, tmp_path, shared):
        other = ExternalSource(label="Other", path=str(tmp_path / "onedrive"),
                               mode="shared-write", professor="smith")
        put_in_shared(shared, records=[a_call(100)], archives=[("2026-05", [a_call(50)])])
        moved = move_a_persons_work("smith", shared, other)
        assert moved.counts == {"calls": 1, "finished months": 1}
        assert tokens_in(other) == 150
        assert tokens_in(shared) == 0


# ---------------------------------------------------------------------------
# What must never happen: losing or double-counting a record
# ---------------------------------------------------------------------------

class TestNothingIsLostOrCountedTwice:

    def test_a_month_at_both_ends_is_added_up_not_replaced(self, here, shared):
        """The case that started this: somebody moved partway through a month,
        with the earlier half here and the later half already there."""
        put_locally(here, records=[a_call(1807)])
        put_in_shared(shared, records=[a_call(214)])
        move_a_persons_work("smith", None, shared)
        assert tokens_in(shared) == 2021

    def test_a_finished_month_at_both_ends_is_added_up(self, here, shared):
        put_locally(here, archives=[("2026-05", [a_call(10)])])
        put_in_shared(shared, archives=[("2026-05", [a_call(90)])])
        move_a_persons_work("smith", None, shared)
        assert tokens_in(shared) == 100

    def test_the_total_survives_a_round_trip(self, here, shared):
        put_locally(here, records=[a_call(100), a_call(200)],
                    archives=[("2026-05", [a_call(50)]), ("2026-06", [a_call(25)])])
        before = tokens_here()
        move_a_persons_work("smith", None, shared)
        move_a_persons_work("smith", shared, None)
        assert tokens_here() == before == 375

    def test_a_summary_with_no_calls_behind_it_is_left_alone(self, here, shared):
        """Its totals cannot be taken apart into calls, and inventing them
        would be making up records. Refused, and said so."""
        (here / "token_usage_smith.json").write_text(json.dumps({
            "month": THIS_MONTH,
            "total_usage": {"total_tokens": 500, "total_cost": 0.5, "prompt_tokens": 250,
                            "completion_tokens": 250, "input_cost": 0.25, "output_cost": 0.25},
            "model_usage": {}, "daily_usage": {}, "session_history": [],
        }))
        moved = move_a_persons_work("smith", None, shared)
        assert moved.left_behind
        assert "by hand" in moved.left_behind[0]
        assert (here / "token_usage_smith.json").exists(), "it was removed anyway"

    def test_nothing_is_removed_until_the_copy_has_been_made(self, here, shared,
                                                             monkeypatch):
        """The order of two lines is the difference between a move that failed
        and a month that no longer exists. A full disk, a folder that has gone
        offline, a sync service holding a file open — the write can fail, and
        when it does the originals have to still be there."""
        put_locally(here, records=[a_call(100)],
                    archives=[("2026-05", [a_call(50)])])

        def cannot_write(*args, **kwargs):
            raise OSError("no space left on device")

        monkeypatch.setattr(relocate, "_write_usage", cannot_write)
        moved = move_a_persons_work("smith", None, shared)

        assert moved.left_behind, "it should say it could not"
        assert not moved.counts, "it should not claim anything moved"
        assert (here / "token_usage_smith.json").exists()
        assert (here / "archives" / "smith" / "2026-05.json").exists()
        assert tokens_here() == 150

    def test_an_unreadable_file_does_not_take_the_rest_with_it(self, here, shared):
        put_locally(here, archives=[("2026-05", [a_call(50)])])
        bad = here / "archives" / "smith" / "2026-06.json"
        bad.write_text("{not json")
        moved = move_a_persons_work("smith", None, shared)
        assert moved.counts["finished months"] == 1
        assert tokens_in(shared) == 50


# ---------------------------------------------------------------------------
# What other parts of the sandbox contribute
# ---------------------------------------------------------------------------

class TestWhatEachPartOfTheSandboxMoves:

    def test_a_registered_mover_is_asked_too(self, here, shared):
        asked = []

        def mover(netid, was, now):
            asked.append((netid, was, now))
            return Moved(counts={"widgets": 3})

        relocate.register_mover(mover, "widgets")
        moved = move_a_persons_work("smith", None, shared)
        assert asked == [("smith", None, shared)]
        assert moved.counts["widgets"] == 3

    def test_it_is_not_asked_when_nothing_changed(self, here):
        asked = []
        relocate.register_mover(lambda *a: asked.append(a) or Moved(), "widgets")
        move_a_persons_work("smith", None, None)
        assert asked == []

    def test_one_part_failing_does_not_strand_the_others(self, here, shared):
        """Usage must still move when conversations cannot, and the person has
        to be told which of the two did not."""
        def broken(netid, was, now):
            raise OSError("the disk is full")

        relocate.register_mover(broken, "widgets")
        put_locally(here, records=[a_call(100)])
        moved = move_a_persons_work("smith", None, shared)
        assert moved.counts["calls"] == 1
        assert any("widgets" in line and "disk is full" in line
                   for line in moved.left_behind)


class TestWhatThePersonIsTold:

    def test_nothing_moved_says_so(self):
        assert Moved().summary() == "There was nothing to move."

    def test_one_of_something_is_not_plural(self):
        assert Moved(counts={"calls": 1}).summary() == "Moved 1 call."

    def test_several_things_are_listed(self):
        said = Moved(counts={"calls": 6, "conversations": 28}).summary()
        assert said == "Moved 6 calls and 28 conversations."

    def test_what_was_left_is_counted_in(self):
        said = Moved(counts={"calls": 1}, left_behind=["a thing"]).summary()
        assert "left where they were" in said


class TestAnEmptiedFileIsNotLeftBehind:
    """A file holding nothing, left where somebody's work used to be, is read
    as a month with nothing in it. That is the same answer a report falls back
    to when the folder their work moved to cannot be reached — so the leftover
    turns "I cannot read that" into a confident zero."""

    def test_it_is_removed_when_the_work_moves_out(self, here, shared):
        (here / "token_usage_smith.json").write_text(json.dumps(a_month([])))
        move_a_persons_work("smith", None, shared)
        assert not (here / "token_usage_smith.json").exists()

    def test_even_though_there_was_nothing_to_move(self, here, shared):
        """It reports honestly: nothing moved, because nothing was in it."""
        (here / "token_usage_smith.json").write_text(json.dumps(a_month([])))
        assert not move_a_persons_work("smith", None, shared)

    def test_a_file_with_something_in_it_is_moved_not_dropped(self, here, shared):
        put_locally(here, records=[a_call(100)])
        move_a_persons_work("smith", None, shared)
        assert not (here / "token_usage_smith.json").exists()
        assert tokens_in(shared) == 100

    def test_and_a_report_then_has_nothing_to_mistake_for_a_zero(self, here, shared):
        (here / "token_usage_smith.json").write_text(json.dumps(a_month([])))
        move_a_persons_work("smith", None, shared)
        assert token_tracker.load_usage_tree(here) == {}


class TestSayingAFolderCannotBeRead:
    """A folder named in the settings and missing from the disk contributes
    nothing — and nothing is also what somebody who has not spent anything
    contributes. A spending report must not leave those looking the same."""

    def test_a_missing_folder_is_reported(self, here, tmp_path):
        settings_store.add_professor("smith", "Prof. Smith", "sk")
        settings_store.set_professor_usage_source(
            "smith", str(tmp_path / "not-mounted"), mode="shared-write")
        missing = token_tracker.unreadable_folders()
        assert [s.professor for s in missing] == ["smith"]

    def test_a_folder_that_is_there_is_not(self, here, tmp_path):
        (tmp_path / "mounted").mkdir()
        settings_store.add_professor("smith", "Prof. Smith", "sk")
        settings_store.set_professor_usage_source(
            "smith", str(tmp_path / "mounted"), mode="shared-write")
        assert token_tracker.unreadable_folders() == []

    def test_an_empty_folder_is_not_called_unreadable(self, here, tmp_path):
        """Present but empty cannot be told apart from "no work yet", and
        claiming otherwise would cry wolf on every new setup."""
        (tmp_path / "mounted").mkdir()
        settings_store.add_professor("smith", "Prof. Smith", "sk")
        settings_store.set_professor_usage_source(
            "smith", str(tmp_path / "mounted"), mode="shared-write")
        assert token_tracker.unreadable_folders() == []

    def test_a_folder_only_being_watched_counts_too(self, here, tmp_path):
        """Read-only still contributes figures to a report, so its absence
        still leaves figures out."""
        settings_store.add_professor("smith", "Prof. Smith", "sk")
        settings_store.set_professor_usage_source(
            "smith", str(tmp_path / "gone"), mode="read-only")
        assert len(token_tracker.unreadable_folders()) == 1

    def test_the_terminal_says_so_before_printing_any_figure(self, here, tmp_path, capsys):
        from src.runtime.info_commands import _warn_about_folders_that_are_not_there

        settings_store.add_professor("smith", "Prof. Smith", "sk")
        settings_store.set_professor_usage_source(
            "smith", str(tmp_path / "not-mounted"), mode="shared-write")
        _warn_about_folders_that_are_not_there("smith")
        said = capsys.readouterr().out
        assert "not there" in said
        assert str(tmp_path / "not-mounted") in said, "it does not say which folder"

    def test_and_says_nothing_when_there_is_nothing_to_say(self, here, tmp_path, capsys):
        from src.runtime.info_commands import _warn_about_folders_that_are_not_there

        settings_store.add_professor("smith", "Prof. Smith", "sk")
        _warn_about_folders_that_are_not_there("smith")
        assert capsys.readouterr().out == ""

    def test_it_only_mentions_the_person_being_reported_on(self, here, tmp_path, capsys):
        from src.runtime.info_commands import _warn_about_folders_that_are_not_there

        for who in ("smith", "jones"):
            settings_store.add_professor(who, who.title(), "sk")
            settings_store.set_professor_usage_source(
                who, str(tmp_path / f"gone-{who}"), mode="shared-write")
        _warn_about_folders_that_are_not_there("smith")
        said = capsys.readouterr().out
        assert "gone-smith" in said
        assert "gone-jones" not in said
