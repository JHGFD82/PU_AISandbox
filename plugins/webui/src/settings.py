"""webui plugin settings.

Defaults come from this plugin's own ``settings.toml``
(``plugins/webui/settings.toml``). Anyone can override any of them without
touching that file — a shared settings file, then ``preferences.toml``, apply
on top under a ``[webui]`` section, in that order. See ``plugin_settings()`` in
``src/settings.py``.

The model lists are the exception to "has a default": there is no fallback for
them in this file, because a list of models is what needs changing when a
provider retires something, and a second copy here would drift out of step
silently. A missing list is a fault in the plugin, reported as such.

Nobody has to open this file, or the plugin's ``settings.toml``, to change any
of it: every setting below is listed in the person's own ``preferences.toml``
for them, commented out, ready to uncomment — see
``src/plugin_preferences.py``.
"""

from src.runtime.model_role import ModelRole
from src.settings import plugin_settings, required_models

_webui = plugin_settings(__file__, "webui")["webui"]

WEBUI_HOST: str = _webui.get("host", "127.0.0.1")
WEBUI_PORT: int = _webui.get("port", 8000)
WEBUI_SESSION_COOKIE_NAME: str = _webui.get("session_cookie_name", "pu_sandbox_session")
WEBUI_COMPACTION_THRESHOLD: float = _webui.get("compaction_threshold", 0.70)
WEBUI_COMPACTION_MODEL: str = _webui.get("compaction_model", "gpt-4o-mini")
# Whether a document supplied to a conversation is kept in that conversation's
# folder afterwards. Off unless someone asks for it: the text is read out and
# kept in the conversation either way, so this is only about having the original
# document to hand — worth the disk for work that may need showing or citing.
WEBUI_KEEP_SUPPLIED_DOCUMENTS: bool = bool(_webui.get("keep_supplied_documents", False))
# Whether the file a job produces is kept in its conversation's folder. On
# unless someone turns it off, in which case it goes to a shared folder of job
# results instead — still downloadable, but no longer part of the conversation
# and no longer deleted along with it.
WEBUI_KEEP_JOB_OUTPUTS: bool = bool(_webui.get("keep_job_outputs", True))

# Both of the above are also settable from the interface, on the settings page,
# which is why the two of them are read through the function below wherever they
# are actually acted on.


def is_on(key: str, default: bool) -> bool:
    """Return what a yes/no setting is set to right now.

    The constants above are read once, when the sandbox starts. That is right
    for anything decided before it runs, and wrong for the two settings a person
    can turn on and off in the interface: ticking a box and being told to
    restart the sandbox before it means anything is not a box worth having.

    So this reads the files again, through the same layers in the same order, at
    the moment the answer is needed. That is a handful of small files, once per
    document supplied or job started — nothing next to the work either of those
    is about to do.

    Args:
        key: The setting's name in the ``[webui]`` section.
        default: What to answer if no file mentions it at all.

    Returns:
        True or False, as set by whichever layer has the last word.
    """
    return bool(plugin_settings(__file__, "webui")["webui"].get(key, default))

# ── Which models each job should use ──────────────────────────────────────────
# Chat needs to read images because a question in the browser can carry a
# document; writing an eight-word title does not.
CHAT_ROLE = ModelRole(
    models=required_models(
        _webui, "chat_models", where="[webui] in plugins/webui/settings.toml",
    ),
    requires_vision=True,
)
TITLE_ROLE = ModelRole(
    models=required_models(
        _webui, "title_models", where="[webui] in plugins/webui/settings.toml",
    ),
)
