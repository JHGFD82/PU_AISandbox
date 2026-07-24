"""Chat service for the webui plugin.

Registered by plugin.py as ``src.services.chat_service`` (the same
convention every other plugin's service uses — see
``plugins/prompt/src/services/prompt_service.py``), which is what makes
``sandbox.chat_service`` available on a ``SandboxProcessor`` without any
change to ``src/runtime/sandbox_processor.py``.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from src.models import get_model_system_role
from src.services.base_service import BaseService
from src.settings import PROMPT_MAX_TOKENS, PROMPT_TEMPERATURE, PROMPT_TOP_P


class ChatService(BaseService):
    """Sends a conversation's message history to the AI model and returns the reply.

    Unlike the one-shot ``PromptService`` (a single user prompt in, a single
    reply out), ``ChatService`` sends the *whole* conversation history on
    every turn, the way a browser-based chat client needs to — the model has
    no memory between requests, so each turn resends everything the model
    should still remember. See ``docs/webui-plugin-plan.md`` section 6 for
    how the web UI keeps that history from growing without bound
    (conversation compaction).
    """

    def send_message(
        self,
        messages: list[dict[str, Any]],
        system_prompt: Optional[str] = None,
    ) -> dict[str, Any]:
        """Send a full conversation to the AI model and return its reply along with billing details.

        Args:
            messages: The conversation so far, oldest first, each a
                      ``{'role': 'user'|'assistant', 'content': <text>}``
                      dictionary — no fields beyond what the AI API itself
                      expects.
            system_prompt: An optional instruction placed ahead of the
                           conversation (e.g. persistent memory notes, once
                           that feature is built). ``None`` sends no system
                           message at all.

        Returns:
            A dictionary with the assistant's reply text (``'content'``),
            the model that actually produced it (``'model'`` — may differ
            from the requested name for a dated alias), and this turn's
            billing details (``'prompt_tokens'``, ``'completion_tokens'``,
            ``'cost'``, all ``None`` if the API didn't report usage).

        Raises:
            RuntimeError: If every retry attempt failed to get a response.
        """
        model = self._get_model()
        temperature, top_p, max_tokens = self._resolve_sampling_params(
            model, PROMPT_TEMPERATURE, PROMPT_TOP_P, PROMPT_MAX_TOKENS,
        )

        api_messages: list[dict[str, Any]] = []
        if system_prompt:
            api_messages.append({"role": get_model_system_role(model), "content": system_prompt})
        api_messages.extend(messages)

        def _attempt(_attempt_num: int) -> Optional[tuple[Any, str]]:
            response = self._create_completion(
                model=model,
                messages=api_messages,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
            )
            content = self._extract_response_content(response)
            return (response, content) if content is not None else None

        result = self._run_with_retry(_attempt, model, operation="webui chat turn")
        response, content = result

        # Recording usage directly here (rather than via the shared
        # _record_response_usage() helper every other service uses) because
        # this needs the TokenUsage object's total_cost back to include in
        # the API response the browser will show — _record_response_usage()
        # only logs and doesn't return anything. The fields recorded are
        # identical either way.
        prompt_tokens: Optional[int] = None
        completion_tokens: Optional[int] = None
        cost: Optional[float] = None
        if (
            response.usage
            and response.usage.prompt_tokens is not None
            and response.usage.completion_tokens is not None
            and response.usage.total_tokens is not None
        ):
            prompt_tokens = response.usage.prompt_tokens
            completion_tokens = response.usage.completion_tokens
            usage = self.token_tracker.record_usage(
                model=response.model or model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=response.usage.total_tokens,
                requested_model=model,
            )
            cost = usage.total_cost
        else:
            logging.warning("webui chat turn: no token usage information in response.")

        return {
            "content": content,
            "model": response.model or model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost": cost,
        }

    def stream_message(
        self,
        messages: list[dict[str, Any]],
        system_prompt: Optional[str] = None,
    ):
        """Send a full conversation to the AI model, yielding the reply as it arrives.

        Same inputs as ``send_message()``, but for a browser chat UI that
        wants to show text appearing live instead of waiting for the whole
        response before showing anything.

        Args:
            messages: Same as ``send_message()``.
            system_prompt: Same as ``send_message()``.

        Yields:
            One ``{'type': 'delta', 'text': str}`` dict per piece of new text
            as it arrives, followed by exactly one final
            ``{'type': 'done', 'content': str, 'model': str,
            'prompt_tokens': int | None, 'completion_tokens': int | None,
            'cost': float | None}`` dict once the model finishes. The billing
            fields are ``None`` if this call's usage wasn't reported (the
            same thing ``send_message()`` already tolerates for a
            non-streamed reply) — the caller should treat that as "this turn
            wasn't billed," not as an error.

        Raises:
            Whatever the underlying API call raises partway through the
            stream. Unlike ``send_message()``, a failure here is never
            retried — see ``BaseService._create_completion_stream()``'s
            docstring for why restarting a partially-delivered stream isn't
            safe. Callers should treat a raised exception as a hard stop and
            surface it, not retry the call themselves.
        """
        model = self._get_model()
        temperature, top_p, max_tokens = self._resolve_sampling_params(
            model, PROMPT_TEMPERATURE, PROMPT_TOP_P, PROMPT_MAX_TOKENS,
        )

        api_messages: list[dict[str, Any]] = []
        if system_prompt:
            api_messages.append({"role": get_model_system_role(model), "content": system_prompt})
        api_messages.extend(messages)

        stream = self._create_completion_stream(
            model=model,
            messages=api_messages,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
        )

        content_parts: list[str] = []
        response_model = model
        prompt_tokens: Optional[int] = None
        completion_tokens: Optional[int] = None
        total_tokens: Optional[int] = None

        for chunk in stream:
            if getattr(chunk, "model", None):
                response_model = chunk.model
            if chunk.choices:
                text = getattr(chunk.choices[0].delta, "content", None)
                if text:
                    content_parts.append(text)
                    yield {"type": "delta", "text": text}
            usage = getattr(chunk, "usage", None)
            if usage:
                prompt_tokens = usage.prompt_tokens
                completion_tokens = usage.completion_tokens
                total_tokens = usage.total_tokens

        cost: Optional[float] = None
        if prompt_tokens is not None and completion_tokens is not None and total_tokens is not None:
            usage_record = self.token_tracker.record_usage(
                model=response_model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                requested_model=model,
            )
            cost = usage_record.total_cost
        else:
            logging.warning("webui chat turn (streamed): no token usage information in final chunk.")

        yield {
            "type": "done",
            "content": "".join(content_parts),
            "model": response_model,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cost": cost,
        }
