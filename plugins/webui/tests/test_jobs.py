"""Tests for plugins/webui/src/jobs.py (registered as _pu_webui_jobs) —
the background job runner behind the composer's plugin actions.

See docs/webui-plugin-plan.md section 10.
"""

from __future__ import annotations

import sys
import time

import pytest

jobs = sys.modules["_pu_webui_jobs"]
conversation = sys.modules["_pu_webui_conversation"]


def _wait_until(predicate, timeout=2.0, interval=0.01):
    """Poll predicate() until it's truthy or timeout elapses; assert it succeeded.

    The fake plugins used below do real (if trivial) work on a background
    thread, so tests can't just check state immediately after start_job()
    returns — start_job() only guarantees the thread has been *launched*,
    not that it has finished.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(interval)
    pytest.fail("Condition not met within timeout.")


class _FakePlugin:
    """A minimal stand-in for a plugin declaring ui_action + run_ui_action."""

    def __init__(self, action_id="translate", run_ui_action=None):
        from src.runtime.ui_action import UiAction
        self.ui_action = UiAction(id=action_id, label="Fake action", command=action_id)
        self.calls = []
        self._run_ui_action = run_ui_action

    def run_ui_action(self, fields, professor, model, on_progress, output_dir):
        self.calls.append((fields, professor, model, on_progress, output_dir))
        return self._run_ui_action(fields, professor, model, on_progress, output_dir)


def _make_store(tmp_path, professor="heller"):
    return conversation.ConversationStore(professor, base_dir=tmp_path)


class TestRealInstalledPluginsExposeUiActions:
    """Regression test for a real bug caught by manual end-to-end testing:
    translation/plugin.py and transcription/plugin.py originally declared
    ``ui_action`` as a bare module-level variable, never attached to the
    ``plugin`` instance that ``load_plugins()`` actually returns. Every
    other test exercising this (including ``_FakePlugin`` above, and each
    plugin's own ``test_..._plugin_ui_action.py``) sets or reads
    ``ui_action`` as an *instance* attribute or reads the module directly —
    neither shape would have caught a plugin that only ever set the module-
    level name. This loads the real ``plugins/`` directory the same way
    ``plugins/webui/src/app.py``'s ``_get_plugins()`` does, so a future
    regression of this exact kind fails here instead of only showing up as
    an empty action list in a running server."""

    def _load_real_plugins(self):
        from pathlib import Path

        from src.runtime import load_plugins

        # plugins/webui/tests/test_jobs.py -> plugins/ is two parents up.
        plugins_dir = Path(__file__).resolve().parents[2]
        return load_plugins(plugins_dir)

    def test_translate_and_transcribe_actions_are_discoverable(self):
        plugins = self._load_real_plugins()
        action_ids = [a.id for a in jobs.list_ui_actions(plugins)]
        assert "translate" in action_ids
        assert "transcribe" in action_ids
        # Regression: transcribe/transcription_review can be wrapped in two
        # different DispatchPlugin instances (if an EA extension plugin is
        # installed) that both proxy to the same underlying ui_action —
        # list_ui_actions() must dedupe by the action itself, not by
        # whichever wrapper object happened to expose it.
        assert action_ids.count("transcribe") == 1
        assert action_ids.count("translate") == 1

    def test_find_plugin_for_action_resolves_real_plugins(self):
        plugins = self._load_real_plugins()
        assert jobs.find_plugin_for_action(plugins, "translate") is not None
        assert jobs.find_plugin_for_action(plugins, "transcribe") is not None


class TestJobStore:
    def test_add_and_get(self):
        store = jobs.JobStore()
        job = jobs.Job(id="job_1", professor="heller", conversation_id="c_1", action_id="translate")
        store.add(job)
        assert store.get("job_1") is job

    def test_get_missing_returns_none(self):
        store = jobs.JobStore()
        assert store.get("nope") is None

    def test_set_status_updates_existing_job(self):
        store = jobs.JobStore()
        job = jobs.Job(id="job_1", professor="heller", conversation_id="c_1", action_id="translate")
        store.add(job)
        store.set_status("job_1", "done")
        assert store.get("job_1").status == "done"

    def test_set_status_records_error(self):
        store = jobs.JobStore()
        job = jobs.Job(id="job_1", professor="heller", conversation_id="c_1", action_id="translate")
        store.add(job)
        store.set_status("job_1", "error", error="boom")
        assert store.get("job_1").error == "boom"

    def test_set_status_on_unknown_job_is_a_no_op(self):
        store = jobs.JobStore()
        store.set_status("nope", "done")  # must not raise


class TestFindPluginForAction:
    def test_finds_matching_plugin(self):
        p = _FakePlugin(action_id="translate")
        assert jobs.find_plugin_for_action({"translate": p}, "translate") is p

    def test_returns_none_when_no_match(self):
        p = _FakePlugin(action_id="translate")
        assert jobs.find_plugin_for_action({"translate": p}, "transcribe") is None

    def test_ignores_plugins_without_ui_action(self):
        class _Plain:
            pass
        assert jobs.find_plugin_for_action({"usage": _Plain()}, "translate") is None

    def test_deduplicates_plugin_registered_under_multiple_commands(self):
        p = _FakePlugin(action_id="translate")
        # Same object under two command keys, e.g. a DispatchPlugin scenario.
        assert jobs.find_plugin_for_action({"a": p, "b": p}, "translate") is p


class TestListUiActions:
    def test_lists_each_distinct_plugin_once(self):
        p1 = _FakePlugin(action_id="translate")
        p2 = _FakePlugin(action_id="transcribe")
        actions = jobs.list_ui_actions({"translate": p1, "transcribe": p2, "translate2": p1})
        ids = sorted(a.id for a in actions)
        assert ids == ["transcribe", "translate"]

    def test_empty_when_no_plugin_declares_one(self):
        class _Plain:
            pass
        assert jobs.list_ui_actions({"usage": _Plain()}) == []

    def test_same_action_via_two_different_wrapper_objects_is_not_duplicated(self):
        # Mirrors the real installation shape once a language extension
        # plugin is present: a plugin owning two commands (e.g.
        # transcribe/transcription_review) gets wrapped in a *separate*
        # DispatchPlugin instance per command, each proxying to the same
        # underlying ui_action (src/runtime/dispatch_plugin.py's
        # __getattr__). Two distinct wrapper objects exposing the identical
        # UiAction object must still only be listed once.
        shared = _FakePlugin(action_id="transcribe")

        class _Wrapper:
            def __init__(self, primary):
                self._primary = primary

            def __getattr__(self, name):
                return getattr(self._primary, name)

        actions = jobs.list_ui_actions({
            "transcribe": _Wrapper(shared),
            "transcription_review": _Wrapper(shared),
        })
        assert len(actions) == 1
        assert actions[0].id == "transcribe"


class TestJobOutputDir:
    def test_creates_and_returns_directory(self, tmp_path):
        d = jobs.job_output_dir("heller", "job_1", base_dir=tmp_path)
        assert d.exists()
        assert d.is_dir()
        assert d == tmp_path / "heller" / "_job_outputs" / "job_1"


class TestStartJob:
    def test_unknown_action_raises_value_error(self, tmp_path):
        store = _make_store(tmp_path)
        store.create(model="gpt-4o")
        with pytest.raises(ValueError, match="No installed plugin"):
            jobs.start_job(
                plugins={}, action_id="translate", fields={}, professor="heller", model=None,
                conversation_id="c_missing", conversation_store=store, job_store=jobs.JobStore(),
            )

    def test_missing_conversation_raises_lookup_error(self, tmp_path):
        store = _make_store(tmp_path)
        p = _FakePlugin(run_ui_action=lambda *a: None)
        with pytest.raises(LookupError):
            jobs.start_job(
                plugins={"translate": p}, action_id="translate", fields={}, professor="heller", model=None,
                conversation_id="c_missing", conversation_store=store, job_store=jobs.JobStore(),
            )

    def test_conversation_already_busy_raises_runtime_error(self, tmp_path):
        store = _make_store(tmp_path)
        conv = store.create(model="gpt-4o")
        conv.active_job_id = "job_existing"
        store.save(conv)
        p = _FakePlugin(run_ui_action=lambda *a: None)
        with pytest.raises(RuntimeError, match="already has a job running"):
            jobs.start_job(
                plugins={"translate": p}, action_id="translate", fields={}, professor="heller", model=None,
                conversation_id=conv.id, conversation_store=store, job_store=jobs.JobStore(),
            )

    def test_conversation_locked_immediately_before_thread_finishes(self, tmp_path):
        # start_job() must set + save active_job_id synchronously, before
        # returning — not as something the background thread does first.
        store = _make_store(tmp_path)
        conv = store.create(model="gpt-4o")
        started = []

        def slow_run(fields, professor, model, on_progress, output_dir):
            started.append(True)
            time.sleep(0.2)
            from src.runtime.ui_action import UiJobResult
            return UiJobResult(output_path=f"{output_dir}/out.txt", output_filename="out.txt", summary="done")

        p = _FakePlugin(run_ui_action=slow_run)
        job = jobs.start_job(
            plugins={"translate": p}, action_id="translate", fields={}, professor="heller", model=None,
            conversation_id=conv.id, conversation_store=store, job_store=jobs.JobStore(),
        )
        reloaded = store.load(conv.id)
        assert reloaded.active_job_id == job.id
        _wait_until(lambda: started)

    def test_success_path_appends_job_result_and_unlocks(self, tmp_path):
        store = _make_store(tmp_path)
        conv = store.create(model="gpt-4o")
        job_store = jobs.JobStore()

        def fake_run(fields, professor, model, on_progress, output_dir):
            from src.runtime.ui_action import UiJobResult
            out_path = f"{output_dir}/translated.txt"
            with open(out_path, "w", encoding="utf-8") as f:
                f.write("translated")
            return UiJobResult(output_path=out_path, output_filename="translated.txt", summary="Translated it.")

        p = _FakePlugin(run_ui_action=fake_run)
        job = jobs.start_job(
            plugins={"translate": p}, action_id="translate", fields={"a": "b"}, professor="heller", model="gpt-4o",
            conversation_id=conv.id, conversation_store=store, job_store=job_store,
        )
        _wait_until(lambda: job_store.get(job.id).status != "running")

        assert job_store.get(job.id).status == "done"
        reloaded = store.load(conv.id)
        assert reloaded.active_job_id is None
        result_msgs = [m for m in reloaded.messages if m.kind == "job_result"]
        assert len(result_msgs) == 1
        assert result_msgs[0].output_filename == "translated.txt"
        assert result_msgs[0].job_id == job.id
        assert result_msgs[0].content == "Translated it."
        # A job-only conversation with the default title gets renamed from
        # the job's own summary rather than staying "New conversation".
        assert reloaded.title == "Translated it."

    def test_existing_title_is_not_overwritten_by_job_completion(self, tmp_path):
        store = _make_store(tmp_path)
        conv = store.create(model="gpt-4o", title="My real title")
        job_store = jobs.JobStore()

        def fake_run(fields, professor, model, on_progress, output_dir):
            from src.runtime.ui_action import UiJobResult
            return UiJobResult(output_path=f"{output_dir}/out.txt", output_filename="out.txt", summary="Done.")

        p = _FakePlugin(run_ui_action=fake_run)
        job = jobs.start_job(
            plugins={"translate": p}, action_id="translate", fields={}, professor="heller", model=None,
            conversation_id=conv.id, conversation_store=store, job_store=job_store,
        )
        _wait_until(lambda: job_store.get(job.id).status != "running")
        assert store.load(conv.id).title == "My real title"

    def test_on_progress_appends_progress_messages_in_order(self, tmp_path):
        store = _make_store(tmp_path)
        conv = store.create(model="gpt-4o")
        job_store = jobs.JobStore()

        def fake_run(fields, professor, model, on_progress, output_dir):
            from src.runtime.ui_action import UiJobResult
            on_progress(1, 3)
            on_progress(2, 3)
            on_progress(3, 3)
            return UiJobResult(output_path=f"{output_dir}/out.txt", output_filename="out.txt", summary="Done.")

        p = _FakePlugin(run_ui_action=fake_run)
        job = jobs.start_job(
            plugins={"translate": p}, action_id="translate", fields={}, professor="heller", model=None,
            conversation_id=conv.id, conversation_store=store, job_store=job_store,
        )
        _wait_until(lambda: job_store.get(job.id).status != "running")

        reloaded = store.load(conv.id)
        progress_msgs = [m for m in reloaded.messages if m.kind == "job_progress"]
        assert len(progress_msgs) == 3
        assert all(m.job_id == job.id for m in progress_msgs)

    def test_error_path_appends_job_error_and_unlocks(self, tmp_path):
        store = _make_store(tmp_path)
        conv = store.create(model="gpt-4o")
        job_store = jobs.JobStore()

        def failing_run(fields, professor, model, on_progress, output_dir):
            raise RuntimeError("something went wrong")

        p = _FakePlugin(run_ui_action=failing_run)
        job = jobs.start_job(
            plugins={"translate": p}, action_id="translate", fields={}, professor="heller", model=None,
            conversation_id=conv.id, conversation_store=store, job_store=job_store,
        )
        _wait_until(lambda: job_store.get(job.id).status != "running")

        assert job_store.get(job.id).status == "error"
        assert job_store.get(job.id).error == "something went wrong"
        reloaded = store.load(conv.id)
        assert reloaded.active_job_id is None
        error_msgs = [m for m in reloaded.messages if m.kind == "job_error"]
        assert len(error_msgs) == 1
        assert "something went wrong" in error_msgs[0].content


class TestSweepStaleJobs:
    def test_clears_active_job_id_and_appends_message(self, tmp_path):
        store = _make_store(tmp_path, professor="heller")
        conv = store.create(model="gpt-4o")
        conv.active_job_id = "job_orphaned"
        store.save(conv)

        cleared = jobs.sweep_stale_jobs(["heller"], lambda professor: _make_store(tmp_path, professor))
        assert cleared == 1
        reloaded = store.load(conv.id)
        assert reloaded.active_job_id is None
        error_msgs = [m for m in reloaded.messages if m.kind == "job_error"]
        assert len(error_msgs) == 1
        assert "interrupted" in error_msgs[0].content

    def test_conversations_without_active_job_are_untouched(self, tmp_path):
        store = _make_store(tmp_path, professor="heller")
        conv = store.create(model="gpt-4o")
        store.save(conv)

        cleared = jobs.sweep_stale_jobs(["heller"], lambda professor: _make_store(tmp_path, professor))
        assert cleared == 0
        assert store.load(conv.id).messages == []

    def test_sweeps_multiple_professors(self, tmp_path):
        heller_store = _make_store(tmp_path, professor="heller")
        smith_store = _make_store(tmp_path, professor="smith")
        c1 = heller_store.create(model="gpt-4o")
        c1.active_job_id = "job_1"
        heller_store.save(c1)
        c2 = smith_store.create(model="gpt-4o")
        c2.active_job_id = "job_2"
        smith_store.save(c2)

        cleared = jobs.sweep_stale_jobs(
            ["heller", "smith"], lambda professor: _make_store(tmp_path, professor)
        )
        assert cleared == 2
        assert heller_store.load(c1.id).active_job_id is None
        assert smith_store.load(c2.id).active_job_id is None
