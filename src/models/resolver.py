"""Model resolution logic — deterministic fallback from requested → preferred → default."""

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
    """Resolve a model name from config using deterministic fallback rules.

    Resolution order:
    1) requested_model (if provided and valid)
    2) prefer_model (if provided and valid) — callers are responsible for
       resolving their own role-specific default (e.g. via
       ``get_default_model("translation")``) and passing it as prefer_model
    3) DEFAULT_FALLBACK_MODEL (a neutral, role-agnostic fallback)
    4) first available compatible model from pricing config

    Args:
        requested_model: Optional user-specified model
        prefer_model: Optional mode-specific preferred model (e.g., from config.defaults.ocr)
        require_vision: Whether selected model must support vision

    Returns:
        Resolved model name

    Raises:
        ValueError: If requested model is invalid/incompatible or no compatible model exists
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
