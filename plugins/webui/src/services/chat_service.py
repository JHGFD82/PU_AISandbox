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

from src.models import DEFAULT_FALLBACK_MODEL, get_available_models, get_model_system_role
from src.services.api_errors import handle_api_errors
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

        # The shared helper every other service uses. It hands back what the
        # call cost, which is what this one additionally needs — the browser
        # shows it under the reply. (This used to be a copy of the helper's
        # body, made because the helper returned nothing; it returns the
        # record now, so the copy is gone.)
        usage = self._record_response_usage(response, model)
        prompt_tokens: Optional[int] = usage.prompt_tokens if usage else None
        completion_tokens: Optional[int] = usage.completion_tokens if usage else None
        cost: Optional[float] = usage.total_cost if usage else None

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
            ValueError: If the model is no longer accessible through the AI
                        gateway (e.g. this installation's license to it was
                        revoked) — the model is removed from
                        ``model_catalog.json`` first, same as every other
                        service's ``handle_api_errors()`` cleanup, so later
                        requests won't try it again.
            Exception: Whatever else the underlying API call raises. Unlike
                       ``send_message()``, a failure here is never retried —
                       see ``BaseService._create_completion_stream()``'s
                       docstring for why restarting a partially-delivered
                       stream isn't safe. Callers should treat a raised
                       exception as a hard stop and surface it, not retry the
                       call themselves.
        """
        model = self._get_model()
        temperature, top_p, max_tokens = self._resolve_sampling_params(
            model, PROMPT_TEMPERATURE, PROMPT_TOP_P, PROMPT_MAX_TOKENS,
        )

        api_messages: list[dict[str, Any]] = []
        if system_prompt:
            api_messages.append({"role": get_model_system_role(model), "content": system_prompt})
        api_messages.extend(messages)

        content_parts: list[str] = []
        response_model = model
        prompt_tokens: Optional[int] = None
        completion_tokens: Optional[int] = None
        total_tokens: Optional[int] = None

        try:
            stream = self._create_completion_stream(
                model=model,
                messages=api_messages,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
            )
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
        except Exception as e:
            # Same classification every other service uses (see
            # plugins/prompt/src/services/prompt_service.py's send_prompt()):
            # if this is the PortKey router saying the model is no longer
            # accessible (e.g. our license to it was revoked), that model is
            # removed from model_catalog.json here so the next request won't
            # try it again, and a clearer message replaces the raw gateway
            # error. Anything else is re-raised unchanged. Not routed through
            # BaseService._run_with_retry() like send_message() is, because a
            # stream that already delivered visible partial text can't be
            # safely retried from scratch (see _create_completion_stream()'s
            # docstring) — but the model-access cleanup still needs to run
            # even without a retry loop around it.
            handle_api_errors(e, model)
            raise

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

    def generate_title(self, messages: list[dict[str, Any]]) -> Optional[str]:
        """Ask a small, inexpensive model to write a short, descriptive title for a conversation.

        Used right after a conversation's first exchange, instead of just
        taking the literal opening words of the user's first message (the
        previous behavior) — a title picked from the opening sentence often
        reads oddly once the conversation moves past it, the same reason
        most chat products generate one instead.

        Deliberately hardcoded to the catalog's cheap fallback model rather
        than whatever (possibly expensive) model the conversation itself is
        using — writing a five-word title doesn't need a frontier model, and
        professors are billed per token for every call this service makes.

        Args:
            messages: The conversation so far, in the same ``{'role',
                      'content'}`` shape ``send_message()``/``stream_message()``
                      take — typically just the first user message and the
                      assistant's reply to it.

        Returns:
            A short title string, or ``None`` if anything about the call
            failed (API error, empty response, etc.). Callers should treat
            ``None`` as "fall back to your own default title," not as a
            fatal error — a working conversation matters far more than a
            clever title for it.
        """
        model = DEFAULT_FALLBACK_MODEL if DEFAULT_FALLBACK_MODEL in get_available_models() else self._get_model()
        excerpt = "\n\n".join(f"{m['role']}: {m['content']}" for m in messages[:4])
        prompt = (
            "Write a short, descriptive title for the conversation below — four to eight words, "
            "title case, no quotation marks, no trailing punctuation. Respond with only the title "
            "and nothing else.\n\n" + excerpt
        )
        try:
            response = self._create_completion(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=20,
            )
        except Exception as e:
            logging.warning(f"Title generation failed, falling back to a truncated title: {e}")
            return None

        self._record_response_usage(response, model)
        content = self._extract_response_content(response)
        if not content:
            return None
        title = content.strip().strip('"').strip("'").strip()
        return title[:80] or None
