"""Prompt fragments for the external_api reference plugin.

All prompt text lives here.  The service imports what it needs.
Fork this file when building plugins that call AI endpoints — keep
every string constant here and never hard-code prompt text in logic.

Pattern notes
-------------
These fragments follow the same conventions as ``plugins/prompt``:

* **Single constant** — use directly in the service.
* **Runtime substitution** — use ``str.format()`` placeholders
  (e.g. ``{api_name}``, ``{user_query}``).
* **Conditional inclusion** — build the full prompt at call-time
  based on CLI flags or context.

See ``plugins/prompt/src/services/prompts/fragments.py`` for the
full pattern catalogue and usage examples.
"""

# ---------------------------------------------------------------------------
# Default system prompt (used when the caller does not supply one via -s)
# ---------------------------------------------------------------------------

DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful assistant. "
    "Respond clearly and concisely."
)

# ---------------------------------------------------------------------------
# Example: API-context persona (uncomment and adapt when forking)
# ---------------------------------------------------------------------------

# Persona with runtime substitution — fill in api_name at call time:
# RESEARCH_PERSONA = (
#     "You are an expert assistant connected to the {api_name} service. "
#     "Use information from that service to support your answers where relevant."
# )
# Usage: RESEARCH_PERSONA.format(api_name="PU AI Sandbox")

# ---------------------------------------------------------------------------
# Example: output-format instruction
# ---------------------------------------------------------------------------

# FORMAT_INSTRUCTION = (
#     "Structure your response as: a one-sentence summary, then bullet points, "
#     "then a brief conclusion."
# )
