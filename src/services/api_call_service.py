"""API call service — sends prompts to any configured OpenAI-compatible endpoint.

This service wraps ``APIService`` with prompt-assembly helpers and provides
a clean interface for the ``api-call`` command and any other code that needs
to send a free-form prompt to a configured API.

All prompt text lives in ``src/services/prompts/api_call_fragments.py`` —
never hard-code prompt strings in this file.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from .api_config import APIConfig
from .api_service import APIService
from ..tracking.token_tracker import TokenTracker
from .prompts.api_call_fragments import DEFAULT_SYSTEM_PROMPT

logger = logging.getLogger(__name__)


class APICallService:
    """Sends prompts to an AI API endpoint and returns the response.

    Wraps ``APIService`` with prompt-assembly helpers and a simple
    ``send_prompt()`` entry point.
    """

    def __init__(
        self,
        config: APIConfig,
        professor: Optional[str] = None,
        token_tracker: Optional[TokenTracker] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> None:
        self.config = config
        self.svc = APIService(
            config,
            professor=professor,
            token_tracker=token_tracker,
            model=model,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
        )

    def build_messages(
        self,
        user_prompt: str,
        system_prompt: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Assemble the messages list from prompt fragments.

        Override in a subclass to add RAG context, conversation history,
        or other dynamic content.
        """
        effective_system = system_prompt or DEFAULT_SYSTEM_PROMPT
        return [
            {"role": "system", "content": effective_system},
            {"role": "user", "content": user_prompt},
        ]

    def send_prompt(
        self,
        user_prompt: str,
        system_prompt: Optional[str] = None,
    ) -> str:
        """Send a prompt to the AI endpoint and return the response.

        Args:
            user_prompt:   The user's message.
            system_prompt: Optional system/developer prompt.  Falls back to
                           ``DEFAULT_SYSTEM_PROMPT`` from api_call_fragments.py.

        Returns:
            The assistant's reply text.
        """
        messages = self.build_messages(user_prompt, system_prompt)
        logger.info(
            f"Sending prompt to {self.config.display_name} "
            f"({len(user_prompt)} chars)"
        )
        return self.svc.chat_completion(messages)
