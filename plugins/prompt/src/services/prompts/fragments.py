"""Prompt plugin — prompt fragments.

All prompt text for this plugin lives here, in one place.  The service
(``prompt_service.py``) imports what it needs — no raw strings should
appear in service code.

Pattern for new plugins
-----------------------
1.  Create ``src/services/prompts/fragments.py`` in your plugin directory.
2.  Define string constants (and/or dicts keyed by behaviour variant) here.
3.  Import them in your service class; never hard-code prompt text in logic.
4.  Add ``str.format()`` placeholders (e.g. ``{topic}``) for anything that
    varies at runtime.  Fill them with ``.format(...)`` at call time.

This file is the **authoritative home** for all prompt text in this plugin.
To change what the model is told, edit here and open a pull request — do not
scatter prompt text across service methods.

Assembling prompts from fragments (examples)
--------------------------------------------
**Single constant** — use directly::

    from .prompts.fragments import DEFAULT_SYSTEM_PROMPT

    system = DEFAULT_SYSTEM_PROMPT

**Multi-part** — join several blocks with a blank line between them::

    from .prompts.fragments import ROLE_BLOCK, FORMAT_INSTRUCTION, SAFETY_REMINDER

    system = "\\n\\n".join([ROLE_BLOCK, FORMAT_INSTRUCTION, SAFETY_REMINDER])

**Runtime substitution** — use ``str.format()`` placeholders::

    from .prompts.fragments import PERSONA_BLOCK

    system = PERSONA_BLOCK.format(
        name=professor,
        role="researcher",
        institution="Princeton University",
    )

**Conditional inclusion** — build the prompt based on runtime flags::

    from .prompts.fragments import BASE_SYSTEM, STRICT_MODE_ADDENDUM

    system = BASE_SYSTEM
    if args.strict:
        system = "\\n\\n".join([system, STRICT_MODE_ADDENDUM])

**Variant dict** — key prompt text by a mode or format setting::

    from .prompts.fragments import OUTPUT_STYLE

    style_hint = OUTPUT_STYLE.get(args.output_format, OUTPUT_STYLE["default"])
    system = "\\n\\n".join([BASE_SYSTEM, style_hint])
"""

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

# The default system prompt used when the user does not supply one via -s.
# Imported by PromptService as a fallback; also exposed in settings.toml as
# ``prompt.default_system_prompt`` so operators can override it without code
# changes.
#
# Replace this text when forking the plugin.  Examples:
#   "You are a concise legal analyst.  Answer in plain English."
#   "You are an expert Python tutor.  Show code examples when helpful."
DEFAULT_SYSTEM_PROMPT = "You are a helpful assistant."

# ---------------------------------------------------------------------------
# Template: additional instruction blocks
# ---------------------------------------------------------------------------
# Uncomment and adapt these patterns as your plugin grows.
# Each constant is a self-contained block you can combine with others above.

# -- Role / persona (no runtime substitution) --------------------------------
# ROLE_BLOCK = (
#     "You are a knowledgeable research assistant specialising in the social sciences."
# )

# -- Persona with runtime substitution (str.format placeholders) -------------
# PERSONA_BLOCK = (
#     "You are assisting {name}, a {role} at {institution}.  "
#     "Tailor your tone and depth of explanation accordingly."
# )
# Usage: PERSONA_BLOCK.format(name="Alice", role="researcher",
#                              institution="Princeton University")

# -- Output-format instruction (plain string) --------------------------------
# FORMAT_INSTRUCTION = (
#     "Structure your response with a one-sentence summary, then bullet points, "
#     "then a brief conclusion."
# )

# -- Output-format variants (dict keyed by format name) ----------------------
# OUTPUT_STYLE: dict[str, str] = {
#     "default": (
#         "Use clear prose with paragraph breaks."
#     ),
#     "bullets": (
#         "Respond entirely in bullet points.  Use sub-bullets for supporting detail."
#     ),
#     "table": (
#         "Where appropriate, use plain-text tables (pipe-separated columns)."
#     ),
# }
# Usage: OUTPUT_STYLE.get(args.output_format, OUTPUT_STYLE["default"])

# -- Safety / scope reminder -------------------------------------------------
# SAFETY_REMINDER = (
#     "If the request falls outside your area of expertise, say so clearly "
#     "rather than speculating.  Do not fabricate citations or statistics."
# )

# -- Conditional add-on (included only when a flag is set) -------------------
# STRICT_MODE_ADDENDUM = (
#     "STRICT MODE: Provide only information you can support with high confidence.  "
#     "Flag any uncertainty explicitly."
# )
