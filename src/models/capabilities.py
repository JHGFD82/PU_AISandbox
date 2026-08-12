"""Finds out what a model can actually do, by asking it, when it is added.

The sandbox has to know several things about a model before it can use one
properly: whether it can look at an image, what it wants the response-length
setting called, which label it expects on a system instruction, and whether it
will accept being told how varied its wording should be. Providers do not
publish any of this in a form that can be read, and the pricing service the
sandbox already talks to reports prices and nothing else.

So the answers used to arrive the slow way. A model started out assumed to be
unable to read images — safe, but wrong for most current models — and the rest
were learned only when a real request failed and the provider objected, in the
middle of somebody's work. Anything left over had to be corrected by hand in
``model_catalog.json``, which is not a reasonable thing to ask of the people
this sandbox is for.

This module asks instead. Adding a model sends it a handful of deliberately
tiny requests, each designed so that the provider's answer settles exactly one
question, and writes what comes back into the catalog. It costs a fraction of a
cent, takes a few seconds, and happens once in a model's life.

The rule throughout: **only record a negative when the provider positively
refused that particular thing.** A timeout, a network failure or a rejected API
key says nothing about what the model can do, and recording "no" from one of
those would reintroduce the exact fault this replaces — a capable model marked
incapable, with no sign of why. Where a question cannot be settled, it is left
unanswered and reported as untested.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from .catalog import is_sampling_param_deprecated_error
from ..services.api_errors import is_transient_error, rejected_request_field

logger = logging.getLogger(__name__)

# How a provider phrases "I will not accept that part of your request". Only an
# error that reads like one of these is allowed to settle a question, because
# only that kind is genuinely about the model. Everything else — a dropped
# connection, a refused key, a rate limit — says nothing about what the model
# can do, and reading one as a refusal is precisely the fault this replaces.
_SOUNDS_LIKE_A_REFUSAL = (
    "unsupported",
    "not supported",
    "does not support",
    "is not permitted",
    "not allowed",
    "unrecognized",
    "unknown parameter",
    "unknown field",
    "extra inputs",
    "invalid content",
    "invalid type",
    "invalid parameter",
    "unexpected keyword",
)

# A model that isn't there. Nothing can be learned from one, and every probe
# against it fails identically — which read as a model that refuses everything
# and got recorded as "tested, text only", complete with a date, for three
# models that do not exist. Checked before anything else.
_NO_SUCH_MODEL = (
    "model_not_found",
    "does not exist",
    "no such model",
    "no longer available",
    "has been retired",
    "invalid target name found in the query router",
)

# The cap was too low, not the wrong name. Reasoning models spend their
# allowance on thinking that never appears in the answer, so a very small cap
# is refused outright — which is not an answer about anything being probed.
_CAP_TOO_LOW = (
    "could not finish the message",
    "model output limit was reached",
)

# Never a refusal, whatever else the message happens to contain. Checked first,
# so an authentication failure that mentions an "invalid" key can't be mistaken
# for the model turning a request field down.
_NOT_ABOUT_THE_MODEL = (
    "connection",
    "timed out",
    "timeout",
    "network",
    "ssl",
    "certificate",
    "authentication",
    "invalid api key",
    "incorrect api key",
    "unauthorized",
    "permission denied",
    "rate limit",
    "quota",
    "too many requests",
    "429",
    "401",
    "403",
)


def _is_a_refusal(error: Exception) -> bool:
    """Say whether an error is the provider turning down part of the request.

    The whole module rests on this distinction. A refusal answers the question
    a probe asked; anything else means the question went unanswered, and
    recording it as "no" would mark a capable model incapable.

    Args:
        error: Whatever the request raised.

    Returns:
        ``True`` only if the error positively reads as a refusal of something
        in the request. When in doubt, ``False`` — an unsettled question is a
        much smaller problem than a wrong answer recorded as fact.
    """
    if is_transient_error(error):
        return False
    message = str(error).lower()
    if any(phrase in message for phrase in _NOT_ABOUT_THE_MODEL):
        return False
    if model_is_missing(error) or _cap_was_too_low(error):
        return False
    # A model that has dropped a sampling setting altogether. Its own helper
    # because the wording is nothing like the others — the gateway says the
    # field is "deprecated for this model", which is a refusal and the exact
    # answer the sampling probe is asking for.
    if is_sampling_param_deprecated_error(str(error)):
        return True
    if rejected_request_field(str(error)):
        return True
    return any(phrase in message for phrase in _SOUNDS_LIKE_A_REFUSAL)


def model_is_missing(error: Exception) -> bool:
    """Say whether the provider's answer means there is no such model.

    Args:
        error: Whatever the request raised.

    Returns:
        ``True`` if the model isn't there — retired, misspelled, or not
        included in this installation's access. Nothing can be learned about a
        model like that, and it needs removing rather than recording.
    """
    return any(phrase in str(error).lower() for phrase in _NO_SUCH_MODEL)


def _cap_was_too_low(error: Exception) -> bool:
    """Say whether the provider refused only because the answer had no room."""
    return any(phrase in str(error).lower() for phrase in _CAP_TOO_LOW)


# A one-pixel transparent PNG, small enough to sit in this file. Enough to
# settle whether a model accepts an image at all, which is the only question
# being asked — nothing here depends on what the picture shows.
_ONE_PIXEL_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

# Enough room for an answer, and no more. This was one token, which is as cheap
# as a request gets and worked everywhere except the models most worth testing:
# a reasoning model spends its allowance on thinking that never reaches the
# reply, so it refuses a cap of one outright. That refusal isn't an answer about
# anything being asked, but it arrived looking like one, and every probe after
# it inherited a request the model had already rejected.
_ENOUGH_TOKENS = 16

# Words that mean a refusal was about the picture rather than anything else in
# the request. Without this the vision question was answered by whatever the
# model happened to object to.
_ABOUT_THE_PICTURE = (
    "image",
    "image_url",
    "content type",
    "multimodal",
    "vision",
    "media",
)

# Said to every probe. Short, harmless, and never shown to anyone.
_HELLO = [{"role": "user", "content": "hi"}]


@dataclass
class CapabilityReport:
    """What was learned about a model, and what could not be.

    Attributes:
        findings: The catalog fields to save, ready to merge into the model's
                  entry — ``supports_vision``, and ``prefers``/``rejects``
                  where anything was found.
        settled: Plain-English lines naming each question that got an answer,
                 for showing to the person who added the model.
        unsettled: Questions that could not be answered, each with the reason.
                   These are left as they were rather than guessed at.
        reachable: Whether the model could be reached at all. ``False`` means
                   nothing was learned and nothing should be written — the key
                   was refused, the network failed, or the model isn't there.
        missing: Whether the provider says there is no such model. Separate
                 from *reachable* because the answer is different: not "try
                 again later" but "this entry is stale, take it out".
    """

    findings: Dict[str, Any] = field(default_factory=dict)
    settled: List[str] = field(default_factory=list)
    unsettled: List[str] = field(default_factory=list)
    reachable: bool = True
    missing: bool = False


class _Asker:
    """Sends the probe requests and remembers what the model has agreed to.

    Each probe builds on the last: once the model has said which name it wants
    for the response-length cap, every later probe has to use that name or it
    will be refused for the wrong reason. This holds those settled answers so
    the probes don't have to pass them between each other.
    """

    def __init__(self, client: Any, model: str) -> None:
        self.client = client
        self.model = model
        # Starts as the name most models want; the first probe may correct it.
        self.max_tokens_field = "max_tokens"
        self.system_role: Optional[str] = None

    def ask(self, **extra: Any) -> tuple[bool, str]:
        """Send one tiny request and report only whether it was accepted.

        Args:
            **extra: What this particular probe wants to add to an otherwise
                     minimal request — an image, a temperature, a system
                     message.

        Returns:
            A pair of ``(accepted, error text)``. ``accepted`` is ``True`` if
            the provider took the request; the error text is empty then, and
            otherwise holds what the provider said, for working out why.

        Raises:
            Exception: If the failure wasn't the provider refusing something —
                       a dropped connection, a refused key, a rate limit. Those
                       say nothing about the model, so they are passed up
                       rather than read as an answer.
        """
        messages = list(extra.pop("messages", _HELLO))
        request: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            self.max_tokens_field: _ENOUGH_TOKENS,
            **extra,
        }
        try:
            self.client.chat.completions.create(**request)
            return True, ""
        except Exception as error:
            if not _is_a_refusal(error):
                raise
            return False, str(error)


def _probe_max_tokens_field(asker: _Asker, report: CapabilityReport) -> bool:
    """Work out what this model wants the response-length setting called.

    Most models call it ``max_tokens``; some reasoning models reject that name
    and require ``max_completion_tokens`` — the same setting, spelled
    differently. This has to run first: every later probe carries this setting,
    so getting the name wrong here makes every one of them fail for a reason
    that has nothing to do with what it was testing.

    Returns:
        Whether the request shape is settled well enough to go on. ``False``
        stops the rest: a probe that can't even be addressed to the model
        answers nothing, and the answers it appeared to give were being kept.
    """
    accepted, error = asker.ask()
    if accepted:
        report.settled.append("Response-length setting: max_tokens")
        return True

    # Only worth a second try if the provider objected to that name specifically.
    if rejected_request_field(error) != "max_tokens" and "max_completion_tokens" not in error:
        report.unsettled.append(f"Could not settle the response-length setting: {error[:160]}")
        return False

    asker.max_tokens_field = "max_completion_tokens"
    accepted, second_error = asker.ask()
    if accepted:
        report.findings.setdefault("prefers", {})["max_tokens_field"] = "max_completion_tokens"
        report.settled.append("Response-length setting: max_completion_tokens")
        return True

    asker.max_tokens_field = "max_tokens"
    report.unsettled.append(
        f"Neither name for the response-length setting was accepted: {second_error[:160]}"
    )
    return False


def _probe_system_role(asker: _Asker, report: CapabilityReport) -> bool:
    """Work out what this model calls the instruction that opens a conversation.

    Most take a ``system`` message. Some reasoning models require the label
    ``developer`` instead and refuse ``system`` outright.
    """
    last_error = ""
    for label in ("system", "developer"):
        accepted, error = asker.ask(
            messages=[{"role": label, "content": "Be brief."}, *_HELLO]
        )
        if accepted:
            if label != "system":
                report.findings.setdefault("prefers", {})["system_role"] = label
            report.settled.append(f"Opening instruction goes in a '{label}' message")
            asker.system_role = label
            return True
        last_error = error
    report.unsettled.append(
        f"Could not settle what to call the opening instruction: {last_error[:160]}"
    )
    return True


def _probe_sampling_params(asker: _Asker, report: CapabilityReport) -> bool:
    """Check whether the model accepts being told how varied its wording should be.

    ``temperature`` and ``top_p`` are tested one at a time, because a model can
    refuse one and accept the other, and a single test would record the wrong
    answer against whichever it wasn't.
    """
    for name, value in (("temperature", 0.5), ("top_p", 0.9)):
        accepted, error = asker.ask(**{name: value})
        if accepted:
            report.settled.append(f"Accepts {name}")
            continue
        refused = rejected_request_field(error)
        if refused == name or name in error:
            report.findings.setdefault("rejects", {})[name] = f"tested on add: {error[:120]}"
            report.settled.append(f"Refuses {name} — it will be left out")
        else:
            report.unsettled.append(f"Could not settle whether {name} is accepted: {error[:140]}")
    return True


def _probe_vision(asker: _Asker, report: CapabilityReport) -> bool:
    """Check whether the model can look at an image.

    This is the question that caused the trouble: chat in the web interface
    requires it, because a question typed in a browser may carry a document,
    and a model wrongly recorded as unable to read images cannot be used there
    at all.
    """
    accepted, error = asker.ask(
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": "hi"},
                {"type": "image_url", "image_url": {"url": _ONE_PIXEL_PNG}},
            ],
        }]
    )
    if accepted:
        report.findings["supports_vision"] = True
        report.settled.append("Can read images")
        return True

    # A refusal only answers this question if it is about the picture. This
    # used to record "text only" for any refusal at all, which meant a model
    # objecting to something else entirely — the name of the response-length
    # setting, say — was written down as unable to see. That is how four
    # perfectly capable models came to be marked text-only in one sweep.
    if not any(word in error.lower() for word in _ABOUT_THE_PICTURE):
        report.unsettled.append(
            f"Could not settle whether it can read images; the refusal was about "
            f"something else: {error[:140]}"
        )
        return True

    report.findings["supports_vision"] = False
    report.settled.append("Cannot read images — text only")
    logger.debug("Vision probe for '%s' was refused: %s", asker.model, error[:200])
    return True


# Run in this order because each depends on the ones before it having settled
# how a request to this model has to be addressed.
_PROBES = (
    _probe_max_tokens_field,
    _probe_system_role,
    _probe_sampling_params,
    _probe_vision,
)


# Long enough for a slow provider to answer sixteen tokens, short enough that
# one unresponsive model cannot hold up the rest. Without it a sweep of the
# catalog stalled for seven minutes on a single request and would have waited
# indefinitely — and in the browser that is a page that never comes back.
_TESTING_TIMEOUT_SECONDS = 60.0


def client_for_testing(api_key: str) -> Any:
    """Build the client the capability tests are sent through.

    Here rather than at each call site so the timeout can't be forgotten by one
    of them — the command line, the browser, and adding a model each need this
    and each used to build their own.

    Args:
        api_key: The professor's API key the test requests are billed to.

    Returns:
        A PortKey client that gives up on a request rather than waiting for one
        that is never coming.
    """
    import httpx
    from portkey_ai import Portkey

    # Both halves, because they stop different things. request_timeout is sent
    # to the gateway and asks *it* to give up; the http_client timeout is
    # enforced here and is what saves us if nothing comes back at all — which
    # is the case that actually stalled. The same http_client pattern is used
    # in BaseService for an endpoint's own settings.
    return Portkey(
        api_key=api_key,
        # Milliseconds, unlike every other timeout in this codebase. Passing
        # seconds here asks the gateway to give up after sixty *thousandths*
        # of a second, which fails nearly every request — and the reply says
        # so plainly ("timeout sent in the request: 60ms"), which is how it
        # was caught.
        request_timeout=int(_TESTING_TIMEOUT_SECONDS * 1000),
        http_client=httpx.Client(timeout=_TESTING_TIMEOUT_SECONDS),
    )


def probe_model_capabilities(model_name: str, client: Any) -> CapabilityReport:
    """Ask a model what it can do, and report the answers.

    Sends a few very small requests — one per question — and reads the
    provider's acceptance or refusal of each. Nothing the model says is used;
    only whether it agreed to answer at all.

    Args:
        model_name: The model to test, named as the sandbox names it
                    (e.g. ``'claude-opus-4-8'``), which is what gets sent in
                    a real request.
        client: Something that can take ``chat.completions.create(...)`` — in
                normal use the same PortKey client the sandbox makes requests
                with, built from a professor's API key.

    Returns:
        A ``CapabilityReport``. If the model could not be reached at all, its
        ``reachable`` is ``False`` and it holds no findings — the caller should
        write nothing rather than record guesses.
    """
    report = CapabilityReport()
    asker = _Asker(client, model_name)

    for probe in _PROBES:
        try:
            if not probe(asker, report):
                # The request shape itself is unsettled, so nothing addressed to
                # this model means anything. Going on would collect refusals of
                # a request the model has already rejected and read them as
                # answers — which is exactly what happened.
                logger.warning(
                    "Stopped testing '%s': the shape of a request to it could not be "
                    "settled, so nothing after that would have been an answer.",
                    model_name,
                )
                break
        except Exception as error:
            if model_is_missing(error):
                # Not a failure to test — there is nothing to test. Said plainly
                # so the entry can be taken out rather than recorded as a model
                # that refuses everything.
                report.missing = True
                report.reachable = False
                report.findings.clear()
                report.settled.clear()
                report.unsettled = [
                    f"There is no such model: {str(error)[:160]}"
                ]
                logger.warning(
                    "'%s' does not exist for this installation, so nothing was "
                    "recorded against it. It can be removed from the catalog.",
                    model_name,
                )
                return report
            # A temporary failure, raised through by ask(). One is enough to
            # stop: the rest would almost certainly fail the same way, and each
            # costs a request. What was learned before this point still stands.
            report.unsettled.append(f"Testing stopped early: {error}")
            if not report.settled:
                report.reachable = False
                report.findings.clear()
            logger.warning(
                "Could not finish testing '%s': %s. What could not be tested is "
                "left unset rather than guessed at.", model_name, error,
            )
            return report

    return report


def apply_capability_report(entry: Dict[str, Any], report: CapabilityReport) -> Dict[str, Any]:
    """Fold what was learned about a model into its catalog entry.

    Args:
        entry: The model's existing catalog entry. Not modified — a new one is
               returned — so a caller that decides not to save can drop it.
        report: What the testing found.

    Returns:
        The entry with the findings applied, and ``last_tested`` set to when
        that happened. An answer that was already in the entry and is still
        true is left alone; ``rejects`` and ``prefers`` merge rather than
        replace, so a quirk learned from a real refusal isn't dropped by a
        later test that didn't happen to hit it.
    """
    if not report.reachable:
        return dict(entry)

    updated = dict(entry)
    # Recorded so that "tested, and it genuinely cannot read images" can be
    # told apart from "nobody ever asked". Those look identical in every other
    # field, and confusing them is how a capable model came to look broken
    # with nothing to suggest anything could be done about it.
    updated["last_tested"] = datetime.now().isoformat(timespec="seconds")
    for key, value in report.findings.items():
        if key in ("rejects", "prefers") and isinstance(value, dict):
            merged = dict(updated.get(key) or {})
            merged.update(value)
            updated[key] = merged
        else:
            updated[key] = value
    return updated
