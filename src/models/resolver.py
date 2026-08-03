"""Chooses which AI model to use for a request, with a clear order of fallbacks."""

import logging
from typing import TYPE_CHECKING, Optional

from ..errors import CLIError
from . import catalog as _catalog
from . import pricing as _pricing

if TYPE_CHECKING:  # pragma: no cover - import only for type checking
    from ..runtime.model_role import ModelRole


def resolve_model(
    requested_model: Optional[str] = None,
    *,
    role: Optional["ModelRole"] = None,
    require_vision: bool = False,
) -> str:
    """Decide which AI model to actually use, given what was requested and what's preferred.

    Not every request specifies a model by name, and even when one is given
    it might not be usable (e.g. it isn't in the price catalog, or it can't
    read images when image input is required). This function works through a
    fixed list of fallbacks until it finds a model that will actually work:

    1. The model explicitly requested, if it's valid and usable.
    2. Each model the calling job asked for, in the order it named them.
    3. The cheapest model in the catalog that fits.
    4. Anything at all that fits, reached only when no model carries a price.

    Args:
        requested_model: The model name the user asked for, if any (e.g.
                         ``'gpt-4o'``). Also accepts ``'provider/model-name'``
                         format (e.g. ``'openai/gpt-4o'``), which triggers
                         automatic price lookup and registration if the model
                         isn't already in the catalog.
        role: What the calling job wants — the models it prefers, best first,
              and whether they have to be able to read images. Belongs to the
              plugin doing the work (see ``src/runtime/model_role.py``); this
              function is told the preference rather than holding one, which is
              what keeps a new plugin from needing a change here. ``None``
              means no preference, so resolution goes straight to price.
        require_vision: Whether the chosen model must be able to read images,
                        not just text. Usually left alone and taken from
                        *role*; passed directly only by callers that have no
                        role, such as the web interface picking a default for a
                        conversation that may carry a document.

    Returns:
        The name of the model to use for this request (e.g. ``'gpt-4o'``).

    Raises:
        CLIError: If a model was explicitly requested but isn't in the catalog
                  or can't read images when this job needs that, or if no
                  usable model can be found at all. A ``CLIError`` rather than
                  a plain error because every one of these messages is written
                  to be read by the person who hit it and says what to change:
                  the command line prints it and stops, and the web interface
                  shows it as the reply. Anything else is treated there as a
                  fault in the sandbox and replaced with a reference code, so
                  raising one would hide the explanation this already has.
    """
    preferred: list[str] = list(role.models) if role is not None else []
    require_vision = require_vision or (role.requires_vision if role is not None else False)

    available_models = _catalog.get_available_models()
    # Two wordings of the same requirement, because they sit in different
    # sentences: one completes "this model is not ___", the other stands alone.
    compatibility_label = "able to read images" if require_vision else "configured"
    none_at_all = (
        "No model in the catalog can read images"
        if require_vision
        else "No models are configured"
    )
    # What to actually do about it. The old wording said to run --list-models,
    # which is a thing to type at a command line — and this same message is
    # shown in the browser, where there isn't one. Both readers can act on
    # this: it names the file, and the one line in it that decides the answer.
    suggestion = (
        "If it can read images, open " + str(_catalog.get_model_catalog_path())
        + ' and set "supports_vision": true on its entry. Models added '
        "automatically start with that turned off, because the pricing service "
        "the sandbox reads doesn't say either way."
        if require_vision
        else "See model_catalog.json for the models this installation knows about."
    )

    def is_compatible(model_name: str) -> bool:
        return _catalog.model_supports_vision(model_name) if require_vision else True

    def resolve_candidate(model_name: Optional[str]) -> Optional[str]:
        if model_name and model_name in available_models and is_compatible(model_name):
            return model_name
        return None

    if requested_model:
        # Handle provider/model format (e.g. "openai/gpt-4o", "google/gemini-2.5-pro")
        if "/" in requested_model:
            provider, model_key = requested_model.split("/", 1)
            if model_key not in available_models:
                try:
                    _pricing.add_model_to_catalog(requested_model)
                    available_models = _catalog.get_available_models()
                    logging.info(
                        f"Auto-registered '{model_key}' from '{provider}' into model_catalog.json."
                    )
                except Exception as e:
                    raise CLIError(
                        f"Could not auto-register '{requested_model}' from PortKey pricing catalog: {e}. "
                        "Add it to model_catalog.json manually instead."
                    ) from e
            requested_model = model_key

        # 1) requested_model (if provided and valid)
        if requested_model not in available_models:
            raise CLIError(
                f"Model '{requested_model}' is not in the catalog. "
                "Edit model_catalog.json to add it, or use "
                "'provider/model-name' format (e.g. 'openai/gpt-4o', "
                "'google/gemini-2.5-pro', 'mistral/mistral-small-latest', "
                "'azure-ai/Llama-3.3-70B-Instruct') to auto-register it."
            )
        if not is_compatible(requested_model):
            raise CLIError(
                f"Custom model '{requested_model}' is not {compatibility_label} for this operation. "
                f"{suggestion}"
            )
        return requested_model

    # 2) the models this job asked for, best first. The list belongs to the
    #    plugin doing the work — this function is told the preference, it does
    #    not hold one, so a new plugin never means editing anything here.
    for position, candidate in enumerate(preferred):
        resolved = resolve_candidate(candidate)
        if resolved:
            if position > 0:
                logging.warning(
                    f"'{preferred[0]}' is no longer available, so '{resolved}' was used "
                    "instead. Change the list in the plugin's settings to choose differently."
                )
            return resolved
    if preferred:
        logging.warning(
            f"None of the models asked for ({', '.join(preferred)}) are available; "
            "falling back to the cheapest one that fits."
        )

    # 3) the cheapest model that can do the job. No model name is written down
    #    here on purpose: a hard-coded fallback stops working the day its
    #    provider retires it, and then every mode that relied on it has to be
    #    reconfigured by hand. Ranking by price instead means the sandbox keeps
    #    working, and errs towards the least expensive option rather than
    #    whichever model happens to sort first.
    cheapest = _catalog.cheapest_model(require_vision=require_vision)
    if cheapest and cheapest not in preferred:
        logging.warning(
            f"No preferred model was available, so '{cheapest}' was chosen as the cheapest "
            f"model in the catalog that is {compatibility_label}. Name the models you want "
            "in the owning plugin's settings to choose deliberately."
        )
        return cheapest

    # 4) anything at all that fits — reached only when no model in the catalog
    #    carries a usable price, so the ranking above had nothing to sort.
    for model in available_models:
        if model in preferred:
            continue
        resolved = resolve_candidate(model)
        if resolved:
            return resolved

    raise CLIError(
        f"{none_at_all}. Available models: {available_models}. "
        "Please update model_catalog.json."
    )
