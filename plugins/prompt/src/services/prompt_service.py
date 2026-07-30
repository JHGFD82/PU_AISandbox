"""Custom prompt service for direct AI interaction.

Template notes for plugin authors
----------------------------------
All prompt text belongs in ``src/services/prompts/fragments.py``, not here.
Import the constants you need and assemble them in ``build_prompts()`` below.

Basic assembly (single constant)::

    from .prompts.fragments import DEFAULT_SYSTEM_PROMPT
    ...  # use DEFAULT_SYSTEM_PROMPT directly

Multi-part assembly (joining several fragments)::

    from .prompts.fragments import ROLE_BLOCK, FORMAT_INSTRUCTION, SAFETY_REMINDER

    system = "\n\n".join([ROLE_BLOCK, FORMAT_INSTRUCTION, SAFETY_REMINDER])

Runtime substitution (``str.format()`` placeholders)::

    from .prompts.fragments import PERSONA_BLOCK

    system = PERSONA_BLOCK.format(name=professor, role="researcher",
                                  institution="Princeton")

Conditional inclusion::

    from .prompts.fragments import BASE_SYSTEM, STRICT_MODE_ADDENDUM

    system = BASE_SYSTEM
    if args.strict:
        system = "\n\n".join([system, STRICT_MODE_ADDENDUM])
"""

import logging
from typing import Any, Optional

from ..models import (  # type: ignore[import]
    get_model_system_role,
)
from ..tracking.token_tracker import TokenTracker  # type: ignore[import]
from .api_errors import handle_api_errors  # type: ignore[import]
from .base_service import BaseService  # type: ignore[import]


# Absolute rather than ``..settings``: this plugin has a settings module of its
# own now, and a relative import from this file would mean that one. Four of
# these five live in the sandbox's own settings; PROMPT_ROLE is this plugin's,
# and reaches src.settings through its __getattr__ delegation.
from src.settings import (  # type: ignore[import]
    DEFAULT_SYSTEM_PROMPT,
    PROMPT_MAX_TOKENS,
    PROMPT_ROLE,
    PROMPT_TEMPERATURE,
    PROMPT_TOP_P,
)


class PromptService(BaseService):
    """Sends custom prompts to the AI model and returns the response."""

    # Which models this service's work should use — see
    # ``src/runtime/model_role.py``. Read by ``BaseService._get_model()``.
    model_role = PROMPT_ROLE

    def __init__(
        self,
        api_key: str,
        professor: Optional[str] = None,
        token_tracker: Optional[TokenTracker] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        top_p: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ):
        super().__init__(api_key, professor, token_tracker, None, model, temperature, top_p, max_tokens)

    def build_prompts(
        self,
        user_prompt: str,
        system_prompt: Optional[str] = None,
    ) -> tuple[str, str]:
        """Return (system_prompt, user_prompt) without calling the API.

        Used by --dry-run mode to preview what would be sent to the model.
        """
        return system_prompt or DEFAULT_SYSTEM_PROMPT, user_prompt

    def _call_api(self, model: str, system_role: str, system_prompt: str, user_prompt: str) -> Any:
        """Call the API with parameters appropriate for the given model."""
        temperature, top_p, max_tokens = self._resolve_sampling_params(
            model, PROMPT_TEMPERATURE, PROMPT_TOP_P, PROMPT_MAX_TOKENS
        )
        messages = [
            {"role": system_role, "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        return self._create_completion(
            model, messages, max_tokens,
            temperature=temperature, top_p=top_p,
        )

    def send_prompt(
        self,
        user_prompt: str,
        system_prompt: Optional[str] = None,
    ) -> str:
        """Send a custom prompt and return the response text."""
        model = self._get_model()
        system_role = get_model_system_role(model)
        effective_system = system_prompt if system_prompt else DEFAULT_SYSTEM_PROMPT

        logging.info(f"Sending custom prompt to model: {model}")
        try:
            response = self._call_api(model, system_role, effective_system, user_prompt)
        except Exception as e:
            handle_api_errors(e, model)
            raise

        self._record_response_usage(response, model)
        return self._extract_response_content(response) or ""
