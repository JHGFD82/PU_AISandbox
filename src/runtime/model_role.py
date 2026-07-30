"""``ModelRole`` — how a plugin says which models its work should use.

Every plugin that calls an AI model has an opinion about which one, and that
opinion belongs to the plugin. The sandbox's job is to honour it and to cope
when a provider retires something; it is not to keep a list of what each
plugin wants, because then adding a plugin would mean editing the sandbox, and
the whole point of plugins is that it doesn't.

A plugin declares one role per distinct job it does — translating a document
and translating a scan are two jobs with different needs — and each role names
the models to try, best first::

    # plugins/myplugin/src/settings.py
    from src.runtime.model_role import ModelRole

    MYPLUGIN_ROLE = ModelRole(
        models=_s["myplugin"].get("models", ["gpt-4o", "gpt-4o-mini"]),
    )

Reading the list from the plugin's own ``settings.toml`` (rather than writing it
straight into the code) is what lets someone change it without editing the
plugin — see ``plugin_settings()`` in ``src/settings.py``.

The same object is then named in ``plugin.py`` so the loader can check it is
there, and handed to ``resolve_model()`` when the work runs. One definition,
used by both.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ModelRole:
    """The models a plugin wants for one job, and what that job needs of them.

    Args:
        models: Model names in preference order, best first. More than one
                matters: providers retire models, and naming a second and third
                choice is what lets the work carry on instead of stopping to be
                reconfigured. Every name should be one you would be content to
                see used — if the whole list is gone, the sandbox falls back to
                the cheapest model that fits, which keeps things working but
                chooses on price alone.
        requires_vision: Whether this job needs a model that can read images.
                         ``True`` for reading a scan or a photograph. Set it and
                         the sandbox will never hand this job a text-only model,
                         however cheap.
    """

    models: list[str] = field(default_factory=list)
    requires_vision: bool = False

    def __post_init__(self) -> None:
        """Reject a role that names nothing, since it cannot be honoured."""
        if not self.models:
            raise ValueError(
                "A ModelRole must name at least one model. If this plugin has no "
                "preference, leave the role out of its model_roles instead of "
                "declaring an empty one."
            )
