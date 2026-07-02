"""Chooses which AI model to use for a request, with a clear order of fallbacks."""

import logging
from typing import Optional

from . import catalog as _catalog
from . import pricing as _pricing


def resolve_model(
    requested_model: Optional[str] = None,
    *,
    prefer_model: Optional[str] = None,
    require_vision: bool = False,
) -> str:
    """Decide which AI model to actually use, given what was requested and what's preferred.

    Not every request specifies a model by name, and even when one is given
    it might not be usable (e.g. it isn't in the price catalog, or it can't
    read images when image input is required). This function works through a
    fixed list of fallbacks until it finds a model that will actually work:

    1. The model explicitly requested, if it's valid and usable.
    2. The mode-specific preferred model (e.g. the translation or OCR
       default), if valid and usable.
    3. A neutral, general-purpose fallback model.
    4. The first usable model found anywhere in the price catalog.

    Args:
        requested_model: The model name the user asked for, if any (e.g.
                         ``'gpt-4o'``). Also accepts ``'provider/model-name'``
                         format (e.g. ``'openai/gpt-4o'``), which triggers
                         automatic price lookup and registration if the model
                         isn't already in the catalog.
        prefer_model: The mode's own default model to fall back to if no
                      model was explicitly requested (e.g. the value
                      configured for OCR or translation).
        require_vision: Whether the chosen model must be able to read images,
                        not just text. Set to ``True`` for image-based
                        commands like OCR.

    Returns:
        The name of the model to use for this request (e.g. ``'gpt-4o'``).

    Raises:
        ValueError: If a model was explicitly requested but isn't valid or
                    doesn't support image input when required, or if no
                    usable model can be found at all.
    """
    available_models = _catalog.get_available_models()
    compatibility_label = "vision-capable" if require_vision else "configured"
    suggestion = (
        "Use --list-models to see which models support vision."
        if require_vision
        else "Use --list-models to see available options."
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
                    raise ValueError(
                        f"Could not auto-register '{requested_model}' from PortKey pricing catalog: {e}. "
                        "Add it to model_catalog.json manually instead."
                    ) from e
            requested_model = model_key

        # 1) requested_model (if provided and valid)
        if requested_model not in available_models:
            raise ValueError(
                f"Model '{requested_model}' is not in the catalog. "
                "Edit model_catalog.json to add it, or use "
                "'provider/model-name' format (e.g. 'openai/gpt-4o', "
                "'google/gemini-2.5-pro', 'mistral/mistral-small-latest', "
                "'azure-ai/Llama-3.3-70B-Instruct') to auto-register it."
            )
        if not is_compatible(requested_model):
            raise ValueError(
                f"Custom model '{requested_model}' is not {compatibility_label} for this operation. "
                f"{suggestion}"
            )
        return requested_model

    # 2) prefer_model (if provided and valid)
    # 3) DEFAULT_FALLBACK_MODEL (neutral, role-agnostic)
    priority_candidates = [candidate for candidate in (prefer_model, _catalog.DEFAULT_FALLBACK_MODEL) if candidate]
    for candidate in priority_candidates:
        resolved = resolve_candidate(candidate)
        if resolved:
            return resolved

    # 4) first available compatible model from pricing config
    for model in available_models:
        if model in priority_candidates:
            continue
        resolved = resolve_candidate(model)
        if resolved:
            return resolved

    raise ValueError(
        f"No {compatibility_label} models available. Available models: {available_models}. "
        "Please update model_catalog.json."
    )
    available_models = _catalog.get_available_models()
    compatibility_label = "vision-capable" if require_vision else "configured"
    suggestion = (
        "Use --list-models to see which models support vision."
        if require_vision
        else "Use --list-models to see available options."
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
                    raise ValueError(
                        f"Could not auto-register '{requested_model}' from PortKey pricing catalog: {e}. "
                        "Add it to model_catalog.json manually instead."
                    ) from e
            requested_model = model_key

        # 1) requested_model (if provided and valid)
        if requested_model not in available_models:
            raise ValueError(
                f"Model '{requested_model}' is not in the catalog. "
                "Edit model_catalog.json to add it, or use "
                "'provider/model-name' format (e.g. 'openai/gpt-4o', "
                "'google/gemini-2.5-pro', 'mistral/mistral-small-latest', "
                "'azure-ai/Llama-3.3-70B-Instruct') to auto-register it."
            )
        if not is_compatible(requested_model):
            raise ValueError(
                f"Custom model '{requested_model}' is not {compatibility_label} for this operation. "
                f"{suggestion}"
            )
        return requested_model

    # 2) prefer_model (if provided and valid)
    # 3) DEFAULT_FALLBACK_MODEL (neutral, role-agnostic)
    priority_candidates = [candidate for candidate in (prefer_model, _catalog.DEFAULT_FALLBACK_MODEL) if candidate]
    for candidate in priority_candidates:
        resolved = resolve_candidate(candidate)
        if resolved:
            return resolved

    # 4) first available compatible model from pricing config
    for model in available_models:
        if model in priority_candidates:
            continue
        resolved = resolve_candidate(model)
        if resolved:
            return resolved

    raise ValueError(
        f"No {compatibility_label} models available. Available models: {available_models}. "
        "Please update model_catalog.json."
    )
