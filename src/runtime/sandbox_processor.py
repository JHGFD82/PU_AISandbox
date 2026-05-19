"""Runtime processing orchestration: wires services and delegates to handler mixins."""

import logging
import sys
from typing import TYPE_CHECKING, Optional, TypedDict

from ..config import get_api_key
from ..errors import CLIError
from ..models import OutputOptions  # noqa: F401 — used in type hints across handler mixins
from ..output.file_output import FileOutputHandler
from ..processors.image_processor import ImageProcessor
from ..processors.pdf_processor import PDFProcessor
from ..tracking.token_tracker import TokenTracker
from .command_runner import _CommandMixin
from .document_handler import _DocumentHandlerMixin
from .image_handler import _ImageHandlerMixin

if TYPE_CHECKING:
    from ..services.api_config import APIConfig

logger = logging.getLogger(__name__)


class _SvcKwargs(TypedDict, total=False):
    """Shared keyword arguments passed to every BaseService subclass."""
    token_tracker: TokenTracker
    model: Optional[str]
    temperature: Optional[float]
    top_p: Optional[float]
    max_tokens: Optional[int]


class SandboxProcessor(_DocumentHandlerMixin, _ImageHandlerMixin, _CommandMixin):
    """Main application class for processing inputs to the Princeton AI Sandbox."""

    def __init__(self, professor_name: str, model: Optional[str] = None,
                 temperature: Optional[float] = None, top_p: Optional[float] = None,
                 max_tokens: Optional[int] = None,
                 api_config: Optional["APIConfig"] = None):
        """Initialize the processor for the specified professor.

        Args:
            professor_name: Professor identifier for API key lookup and token tracking.
            model:          Override model name.  May include colon syntax such as
                            ``della:qwen-preview`` — the processor automatically parses
                            the api name prefix and loads the corresponding ``APIConfig``.
            temperature:    Sampling temperature override.
            top_p:          Nucleus-sampling top-p override.
            max_tokens:     Max completion tokens override.
            api_config:     Explicit ``APIConfig`` override.  If ``None`` and ``model``
                            contains colon syntax, the config is resolved automatically.
                            When set, lazily-loaded ``BaseService`` instances will have
                            their Portkey client replaced with an OpenAI-compatible
                            client pointed at this API's ``base_url``.
        """
        try:
            api_key, self.professor_display_name = get_api_key(professor_name)
            self.professor_name = professor_name

            logger.debug(f"Initializing processor for professor: {self.professor_display_name}")

            # Parse colon syntax from model (e.g. "della:qwen-preview") when
            # no explicit api_config has been supplied.
            if api_config is None and model and ":" in model:
                from ..services.api_config import parse_model_source, load_api_config, get_default_api_name
                api_name, bare_model = parse_model_source(model)
                if api_name:
                    try:
                        api_config = load_api_config(api_name)
                        model = bare_model
                    except ValueError as e:
                        raise CLIError(f"API configuration error: {e}") from e
            elif api_config is None:
                from ..services.api_config import get_default_api_name, load_api_config
                default = get_default_api_name()
                if default:
                    try:
                        api_config = load_api_config(default)
                    except ValueError:
                        pass  # misconfigured default — fall through to sandbox

            self.token_tracker = TokenTracker(professor=professor_name)
            self._api_key = api_key
            self._api_config = api_config
            self._svc_kwargs: _SvcKwargs = {
                "token_tracker": self.token_tracker,
                "model": model,
                "temperature": temperature,
                "top_p": top_p,
                "max_tokens": max_tokens,
            }

            # translation_service, image_processor_service, image_translation_service,
            # and transcription_review_service are loaded lazily via __getattr__ so that
            # SandboxProcessor can be instantiated without the plugin service files present.

            self.image_processor = ImageProcessor()
            self.pdf_processor = PDFProcessor()
            self.file_output = FileOutputHandler()
        except ValueError as e:
            raise CLIError(f"Configuration error: {e}") from e

    def __getattr__(self, name: str):
        """Lazily instantiate plugin-provided services on first access.

        Any plugin that injects a module into ``sys.modules`` under the key
        ``src.services.<name>`` and exports a class whose name is the
        PascalCase form of ``<name>`` will be instantiated automatically —
        no changes to this file required.

        When ``self._api_config`` is set, the created service's Portkey client
        is replaced with an OpenAI-compatible client pointing at the configured
        ``base_url``.  The model is also resolved directly (bypassing the model
        catalog) so that external model names like ``qwen-preview`` work as-is.
        """
        module_name = f"src.services.{name}"
        module = sys.modules.get(module_name)
        if module is None:
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")
        class_name = "".join(part.capitalize() for part in name.split("_"))
        cls = getattr(module, class_name, None)
        if cls is None:
            raise AttributeError(
                f"Service module '{module_name}' has no class '{class_name}'"
            )
        val = cls(self._api_key, self.professor_name, **self._svc_kwargs)

        # When routing to an alternate API endpoint, swap the Portkey client
        # with a standard OpenAI client so that any OpenAI-compatible endpoint
        # is used transparently — no changes to the service subclasses needed.
        api_config = object.__getattribute__(self, "_api_config")
        if api_config is not None and hasattr(val, "client"):
            from openai import OpenAI
            val.client = OpenAI(
                api_key=api_config.api_key,
                base_url=api_config.base_url,
                timeout=float(api_config.timeout),
            )
            # Bypass the model catalog for alternate endpoint models: return the
            # model name as-is rather than going through resolve_model().
            configured_model: Optional[str] = self._svc_kwargs.get("model") or api_config.default_model
            if configured_model:
                val._get_model = lambda: configured_model  # type: ignore[method-assign]

        # Cache on the instance so __getattr__ is only called once per service.
        object.__setattr__(self, name, val)
        return val

    def process_transcription_review(
        self,
        text: str,
        language: str,
        kanbun: bool = False,
        kanbun_main: bool = False,
        output_file: Optional[str] = None,
    ) -> None:
        """Review a transcription for OCR errors and print (and optionally save) the JSON report."""
        try:
            result_json = self.transcription_review_service.review_transcription(
                text, language, kanbun=kanbun, kanbun_main=kanbun_main
            )
            print("\n" + result_json)
            if output_file:
                FileOutputHandler.save_to_text_file(result_json, output_file, label="Review")
        except Exception as e:
            logger.error(f"Error during transcription review: {e}", exc_info=True)
            raise CLIError(f"Error during transcription review: {e}") from e
