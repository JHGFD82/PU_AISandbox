"""Tests for finding out what a model can do by asking it.

The behaviour these protect is mostly about restraint. Testing a model is easy;
what is hard is not recording an answer when the test didn't actually produce
one. A model that failed to answer because the network dropped must not come
out of this marked unable to read images — that is the fault the whole module
exists to remove, and it would be reintroduced silently.
"""

import httpx
import pytest

from src.models.capabilities import (
    CapabilityReport,
    apply_capability_report,
    probe_model_capabilities,
)


class FakeModel:
    """Stands in for a provider, refusing whatever it is told to refuse.

    Args:
        refuses: Field names this model rejects, as a provider would phrase it.
        wants_max_completion_tokens: Whether it insists on the other name for
                                     the response-length setting.
        system_role: The label it accepts on an opening instruction.
        sees_images: Whether it accepts a request carrying a picture.
    """

    def __init__(self, refuses=(), wants_max_completion_tokens=False,
                 system_role="system", sees_images=True):
        self.chat = self
        self.completions = self
        self.requests = []
        self._refuses = set(refuses)
        self._wants_mct = wants_max_completion_tokens
        self._system_role = system_role
        self._sees_images = sees_images

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if self._wants_mct and "max_tokens" in kwargs:
            raise Exception(
                "Unsupported parameter: 'max_tokens' is not supported with this "
                "model. Use 'max_completion_tokens' instead."
            )
        if not self._wants_mct and "max_completion_tokens" in kwargs:
            raise Exception("Unrecognized request argument supplied: max_completion_tokens")
        for message in kwargs.get("messages", []):
            role = message.get("role")
            if role in ("system", "developer") and role != self._system_role:
                raise Exception(
                    f"Unsupported value: 'messages[0].role' does not support '{role}' "
                    "with this model."
                )
            content = message.get("content")
            if isinstance(content, list) and not self._sees_images:
                raise Exception("Invalid content type. image_url is only supported by certain models.")
        for name in self._refuses:
            if name in kwargs:
                raise Exception(f"Unsupported value: '{name}' is not supported with this model.")
        return {"choices": []}


class AlwaysFails:
    """A provider that never answers, for whatever reason it is given."""

    def __init__(self, error):
        self.chat = self
        self.completions = self
        self.error = error
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        raise self.error


class TestAnOrdinaryModel:
    def test_it_is_recorded_as_able_to_read_images(self):
        report = probe_model_capabilities("plain-model", FakeModel())
        assert report.findings["supports_vision"] is True

    def test_nothing_odd_is_recorded_against_it(self):
        """A model with no quirks should add no quirks to the catalogue."""
        report = probe_model_capabilities("plain-model", FakeModel())
        assert "rejects" not in report.findings
        assert "prefers" not in report.findings

    def test_every_question_gets_an_answer(self):
        report = probe_model_capabilities("plain-model", FakeModel())
        assert report.unsettled == []
        assert report.reachable is True


class TestAModelThatCannotSee:
    def test_it_is_recorded_as_text_only(self):
        report = probe_model_capabilities("text-only", FakeModel(sees_images=False))
        assert report.findings["supports_vision"] is False

    def test_the_refusal_does_not_stop_the_other_answers(self):
        report = probe_model_capabilities("text-only", FakeModel(sees_images=False))
        assert report.reachable is True
        assert any("Cannot read images" in line for line in report.settled)


class TestAReasoningModel:
    """The awkward case: a different name, a different role, no sampling."""

    @pytest.fixture
    def report(self):
        return probe_model_capabilities("reasoning-model", FakeModel(
            refuses=("temperature", "top_p"),
            wants_max_completion_tokens=True,
            system_role="developer",
        ))

    def test_it_learns_the_other_name_for_the_length_setting(self, report):
        assert report.findings["prefers"]["max_tokens_field"] == "max_completion_tokens"

    def test_it_learns_the_other_label_for_the_opening_instruction(self, report):
        assert report.findings["prefers"]["system_role"] == "developer"

    def test_it_learns_both_sampling_settings_separately(self, report):
        assert set(report.findings["rejects"]) == {"temperature", "top_p"}

    def test_it_still_settles_whether_the_model_can_see(self, report):
        """The earlier quirks must not derail the question that follows them."""
        assert report.findings["supports_vision"] is True

    def test_later_probes_use_the_name_the_model_asked_for(self):
        """Once corrected, every following request has to carry the right name.

        Otherwise each later probe fails for a reason that has nothing to do
        with what it was testing, and its answer is recorded wrongly.
        """
        model = FakeModel(wants_max_completion_tokens=True)
        probe_model_capabilities("reasoning-model", model)
        after_correction = model.requests[1:]
        assert after_correction
        assert all("max_completion_tokens" in r for r in after_correction)


class TestAModelThatRefusesOnlyOneSamplingSetting:
    def test_the_other_one_is_not_recorded_as_refused(self):
        """Testing them together would blame both for one refusal."""
        report = probe_model_capabilities("picky", FakeModel(refuses=("temperature",)))
        assert set(report.findings.get("rejects", {})) == {"temperature"}


class TestWhenTheTestCannotBeRun:
    """The restraint that matters. None of these may record an answer."""

    def test_a_network_failure_records_nothing(self):
        client = AlwaysFails(httpx.ConnectError("connection refused"))
        report = probe_model_capabilities("unreachable", client)
        assert report.reachable is False
        assert report.findings == {}

    def test_a_timeout_does_not_mark_a_model_text_only(self):
        """The exact fault this module replaces: a capable model marked blind."""
        client = AlwaysFails(httpx.ReadTimeout("timed out"))
        report = probe_model_capabilities("slow", client)
        assert "supports_vision" not in report.findings

    def test_it_gives_up_rather_than_paying_for_every_probe(self):
        """One failed request is enough to know the rest will fail too."""
        client = AlwaysFails(httpx.ConnectError("connection refused"))
        probe_model_capabilities("unreachable", client)
        assert client.calls == 1

    def test_it_says_why_nothing_was_settled(self):
        client = AlwaysFails(httpx.ConnectError("connection refused"))
        report = probe_model_capabilities("unreachable", client)
        assert report.unsettled


class TestFoldingTheAnswersIntoTheCatalogue:
    def test_a_quirk_learned_from_a_real_refusal_is_not_dropped(self):
        """Testing doesn't hit every field, so it must not replace the list.

        stream_options is learned in production from a provider's refusal and
        is not among the things tested here. Replacing rejects wholesale would
        lose it and bring back the failure that taught it.
        """
        entry = {"input": 1.0, "rejects": {"stream_options": "learned in production"}}
        report = CapabilityReport(findings={"rejects": {"temperature": "tested on add"}})
        merged = apply_capability_report(entry, report)
        assert merged["rejects"] == {
            "stream_options": "learned in production",
            "temperature": "tested on add",
        }

    def test_pricing_and_other_fields_survive(self):
        entry = {"input": 2.5, "output": 10.0, "portkey_id": "openai/gpt-4o"}
        report = CapabilityReport(findings={"supports_vision": True})
        merged = apply_capability_report(entry, report)
        assert merged["input"] == 2.5
        assert merged["portkey_id"] == "openai/gpt-4o"

    def test_an_unreachable_model_changes_nothing(self):
        entry = {"input": 1.0, "supports_vision": True}
        report = CapabilityReport(findings={"supports_vision": False}, reachable=False)
        assert apply_capability_report(entry, report) == entry

    def test_the_original_entry_is_left_alone(self):
        """A caller that decides not to save must not have changed anything."""
        entry = {"input": 1.0}
        apply_capability_report(entry, CapabilityReport(findings={"supports_vision": True}))
        assert entry == {"input": 1.0}


class TestTheCostOfTesting:
    def test_a_probe_asks_for_barely_any_output(self):
        """These requests are billed to a professor's key.

        A ceiling rather than an exact figure: this asserted exactly one token,
        which is what a reasoning model refuses outright — the assertion was
        holding the bug in place. What matters is that it stays negligible.
        """
        model = FakeModel()
        probe_model_capabilities("plain-model", model)
        for request in model.requests:
            length = request.get("max_tokens", request.get("max_completion_tokens"))
            assert 0 < length <= 32

    def test_an_ordinary_model_is_settled_in_a_handful_of_requests(self):
        model = FakeModel()
        probe_model_capabilities("plain-model", model)
        assert len(model.requests) <= 6

    @pytest.mark.parametrize("message", [
        "Error code: 401 - Invalid API key provided",
        "Error code: 429 - Rate limit reached for this model",
        "Error code: 403 - You do not have permission to use this model",
        "Connection error while reaching the provider",
        "Error code: 503 - Service Unavailable",
    ])
    def test_a_failure_that_is_not_the_model_settles_nothing(self, message):
        """None of these are the model saying what it can do.

        An invalid key mentions the word 'invalid'; a rate limit is a refusal
        of a sort. Reading either as the model turning down a request field is
        how a working model ends up recorded as broken.
        """
        report = probe_model_capabilities("innocent", AlwaysFails(Exception(message)))
        assert report.findings == {}
        assert report.reachable is False


class TestTheRefusalTestItself:
    """The single distinction the whole module rests on."""

    @pytest.mark.parametrize("message", [
        "Unsupported parameter: 'max_tokens' is not supported with this model.",
        "Invalid content type. image_url is only supported by certain models.",
        "Extra inputs are not permitted",
        "Unrecognized request argument supplied: stream_options",
    ])
    def test_a_provider_turning_down_a_field_counts(self, message):
        from src.models.capabilities import _is_a_refusal
        assert _is_a_refusal(Exception(message)) is True

    @pytest.mark.parametrize("message", [
        "Invalid API key provided: sk-abc",
        "Request timed out after 30s",
        "Connection refused",
        "Error code: 500 - internal server error",
        "Error code: 429 - too many requests",
    ])
    def test_everything_else_does_not(self, message):
        from src.models.capabilities import _is_a_refusal
        assert _is_a_refusal(Exception(message)) is False


class TestRecordingThatATestHappened:
    """Telling "found to be text-only" apart from "nobody ever asked".

    Every other field looks identical in those two cases. Without this the
    settings page cannot offer to test the second, because it cannot see it.
    """

    def test_a_completed_test_is_dated(self):
        report = CapabilityReport(findings={"supports_vision": False})
        assert apply_capability_report({}, report)["last_tested"]

    def test_a_model_that_could_not_be_reached_is_not(self):
        report = CapabilityReport(reachable=False)
        assert "last_tested" not in apply_capability_report({}, report)

    def test_a_tested_model_with_no_quirks_at_all_still_counts_as_tested(self):
        """Nothing found is a result, not an absence of one."""
        merged = apply_capability_report({}, CapabilityReport(
            findings={"supports_vision": True}, settled=["Can read images"]))
        assert merged["last_tested"]


class TestTheSweepOf2026_08_03:
    """The three faults a real sweep of 28 models exposed, all at once.

    Every one of them wrote a confident wrong answer rather than declining to
    answer — the failure mode this module was built to prevent, arriving by
    routes the first version didn't consider.
    """

    def test_a_refusal_about_something_else_does_not_settle_vision(self):
        """gpt-5, gpt-5.1, gpt-5.2, gpt-5.4 and o3-mini were marked text-only.

        None of them had refused an image. They had refused the *name of the
        response-length setting*, and the vision probe recorded "cannot read
        images" from that — an answer to a question nobody asked. So the
        refusal here says nothing about pictures.
        """
        class RefusesTheImageRequestForAnotherReason:
            def __init__(self):
                self.chat = self
                self.completions = self

            def create(self, **kwargs):
                carries_a_picture = any(
                    isinstance(m.get("content"), list) for m in kwargs.get("messages", [])
                )
                if carries_a_picture:
                    raise Exception(
                        "Unsupported parameter: 'max_tokens' is not supported with "
                        "this model."
                    )
                return {"choices": []}

        report = probe_model_capabilities("misleading", RefusesTheImageRequestForAnotherReason())
        assert "supports_vision" not in report.findings
        assert any("something else" in line for line in report.unsettled)

    def test_a_refusal_that_is_about_the_picture_still_settles_it(self):
        """The fix must not make the question unanswerable."""
        report = probe_model_capabilities("text-only", FakeModel(sees_images=False))
        assert report.findings["supports_vision"] is False

    def test_a_cap_of_one_token_is_not_read_as_a_refusal(self):
        """A reasoning model spends its allowance before it can reply.

        'Could not finish the message because max_tokens was reached' is the
        cap being too low, not the field being wrong. Read as a refusal, it
        made the probe give up on the request shape and mark the model as
        refusing everything after it.
        """
        from src.models.capabilities import _is_a_refusal
        assert _is_a_refusal(Exception(
            "Error code: 400 - {'error': {'message': 'Could not finish the message "
            "because max_tokens or model output limit was reached.', 'type': "
            "'invalid_request_error'}}"
        )) is False

    def test_the_error_envelope_alone_is_not_a_refusal(self):
        """'invalid_request_error' is the type on every 4xx body, 404 included.

        Matching it made a missing model look like one refusing every field.
        """
        from src.models.capabilities import _is_a_refusal
        assert _is_a_refusal(Exception(
            "Error code: 404 - {'error': {'message': 'The model `gpt-35-turbo-16k` "
            "does not exist or you do not have access to it.', 'type': "
            "'invalid_request_error', 'code': 'model_not_found'}}"
        )) is False

    def test_a_model_that_does_not_exist_is_said_to_be_missing(self):
        """gpt-35-turbo, gpt-35-turbo-16k and gpt-4-32k are all gone.

        All three were recorded as tested and text-only, with a date, because
        every probe failed identically and that read as a model refusing
        everything.
        """
        client = AlwaysFails(Exception(
            "Error code: 404 - The model `gpt-4-32k` does not exist or you do not "
            "have access to it."
        ))
        report = probe_model_capabilities("gpt-4-32k", client)
        assert report.missing is True
        assert report.reachable is False
        assert report.findings == {}
        assert report.settled == []

    def test_a_missing_model_costs_one_request_not_five(self):
        client = AlwaysFails(Exception("Error code: 404 - model_not_found"))
        probe_model_capabilities("gone", client)
        assert client.calls == 1

    def test_nothing_is_written_for_a_missing_model(self):
        """Its existing entry must survive untouched, ready to be removed."""
        entry = {"input": 1.0, "supports_vision": True}
        report = CapabilityReport(reachable=False, missing=True)
        assert apply_capability_report(entry, report) == entry

    def test_an_unsettled_request_shape_stops_the_rest(self):
        """Probes after an unaddressable request collect meaningless refusals.

        They were being kept as answers. One request to find out, then stop.
        """
        class RefusesBothNames:
            def __init__(self):
                self.chat = self
                self.completions = self
                self.calls = 0
            def create(self, **kwargs):
                self.calls += 1
                raise Exception("Unsupported parameter: max_tokens is not supported. "
                                "Use 'max_completion_tokens' instead.")

        client = RefusesBothNames()
        report = probe_model_capabilities("impossible", client)
        assert client.calls == 2
        assert "supports_vision" not in report.findings

    def test_the_cap_leaves_room_for_a_reasoning_model_to_answer(self):
        model = FakeModel()
        probe_model_capabilities("plain-model", model)
        caps = [r.get("max_tokens", r.get("max_completion_tokens")) for r in model.requests]
        assert all(c >= 16 for c in caps), caps

    @pytest.mark.parametrize("message,label", [
        ("azure-ai error: `temperature` is deprecated for this model.", "refusal"),
        ("azure-ai error: `top_p` is deprecated for this model.", "refusal"),
    ])
    def test_a_dropped_sampling_setting_is_a_refusal(self, message, label):
        """The Claude models say it this way, and it is the answer being sought.

        Trimming the refusal phrases to stop a 404 matching also stopped this
        matching, so testing gave up at the temperature probe and never reached
        the question about images.
        """
        from src.models.capabilities import _is_a_refusal
        assert _is_a_refusal(Exception(message)) is True

    @pytest.mark.parametrize("message", [
        "google error: This model models/gemini-3-pro-preview is no longer available.",
        "The model `gpt-4-32k` does not exist or you do not have access to it.",
        "invalid target name found in the query router",
    ])
    def test_every_way_a_provider_says_the_model_is_gone(self, message):
        """Three providers, three wordings, one meaning."""
        from src.models.capabilities import model_is_missing
        assert model_is_missing(Exception(message)) is True

    def test_a_model_that_drops_sampling_is_still_asked_about_images(self):
        """The whole point: one refusal must not end the questioning."""
        model = FakeModel(refuses=("temperature", "top_p"))
        report = probe_model_capabilities("claude-like", model)
        assert report.findings["supports_vision"] is True
        assert set(report.findings["rejects"]) == {"temperature", "top_p"}
