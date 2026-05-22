"""Public API for the models package.

Re-exports every symbol from the three submodules so callers can use either
``from src.models import X`` or ``from src.models.catalog import X``.
"""

from .doc_block import ParagraphBlock, TableBlock
from .embedded_media import EmbeddedMedia
from .catalog import (
    DEFAULT_FALLBACK_MODEL,
    MODEL_CATALOG_FILE,
    get_available_models,
    get_default_model,
    get_model_catalog_path,
    get_model_max_completion_tokens,
    get_model_pricing,
    get_model_system_role,
    get_monthly_limit,
    get_pricing_unit,
    get_vision_capable_models,
    is_model_access_error,
    load_model_catalog,
    model_has_fixed_parameters,
    model_omit_sampling_params,
    model_supports_vision,
    model_uses_max_completion_tokens,
    remove_model_from_catalog,
    save_model_catalog,
)
from .output_options import OutputOptions
from .pricing import (
    PORTKEY_PRICING_API_BASE,
    add_model_to_catalog,
    maybe_sync_model_pricing,
)
from .resolver import resolve_model

__all__ = [
    # catalog
    "MODEL_CATALOG_FILE",
    "DEFAULT_FALLBACK_MODEL",
    "get_model_catalog_path",
    "load_model_catalog",
    "save_model_catalog",
    "get_available_models",
    "get_default_model",
    "get_model_pricing",
    "get_pricing_unit",
    "get_monthly_limit",
    "model_supports_vision",
    "get_vision_capable_models",
    "get_model_system_role",
    "model_uses_max_completion_tokens",
    "model_has_fixed_parameters",
    "model_omit_sampling_params",
    "get_model_max_completion_tokens",
    # catalog (continued)
    "is_model_access_error",
    "remove_model_from_catalog",
    # pricing
    "PORTKEY_PRICING_API_BASE",
    "add_model_to_catalog",
    "maybe_sync_model_pricing",
    # resolver
    "resolve_model",
    # output options
    "OutputOptions",
    # embedded media
    "EmbeddedMedia",
    # doc blocks
    "ParagraphBlock",
    "TableBlock",
]
