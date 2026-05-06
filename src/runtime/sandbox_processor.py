"""Runtime processing orchestration: wires services and delegates to handler mixins."""

import logging
from typing import TYPE_CHECKING, Optional, TypedDict

from ..config import get_api_key
from ..errors import CLIError
from ..models import OutputOptions  # noqa: F401 — used in type hints across handler mixins
from ..output.file_output import FileOutputHandler
from ..processors.image_processor import ImageProcessor
from ..processors.pdf_processor import PDFProcessor
from ..services.prompt_service import PromptService
from ..tracking.token_tracker import TokenTracker
from .command_runner import _CommandMixin
from .document_handler import _DocumentHandlerMixin
from .image_handler import _ImageHandlerMixin

if TYPE_CHECKING:
    from ..services.image_processor_service import ImageProcessorService
    from ..services.image_translation_service import ImageTranslationService
    from ..services.transcription_review_service import TranscriptionReviewService
    from ..services.translation_service import TranslationService

logger = logging.getLogger(__name__)

# Services provided by plugins (loaded lazily via __getattr__).
_LAZY_SERVICES: dict[str, str] = {
    "translation_service": "..services.translation_service.TranslationService",
    "image_processor_service": "..services.image_processor_service.ImageProcessorService",
    "image_translation_service": "..services.image_translation_service.ImageTranslationService",
    "transcription_review_service": "..services.transcription_review_service.TranscriptionReviewService",
}


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
                 max_tokens: Optional[int] = None):
        """Initialize the processor for the specified professor."""
        try:
            api_key, self.professor_display_name = get_api_key(professor_name)
            self.professor_name = professor_name

            logger.debug(f"Initializing processor for professor: {self.professor_display_name}")

            self.token_tracker = TokenTracker(professor=professor_name)
            self._api_key = api_key
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
            self.prompt_service = PromptService(api_key, professor_name, **self._svc_kwargs)

            self.image_processor = ImageProcessor()
            self.pdf_processor = PDFProcessor()
            self.file_output = FileOutputHandler()
        except ValueError as e:
            raise CLIError(f"Configuration error: {e}") from e

    def __getattr__(self, name: str):
        """Lazily instantiate plugin-provided services on first access."""
        if name == "translation_service":
            from ..services.translation_service import TranslationService
            val = TranslationService(self._api_key, self.professor_name, **self._svc_kwargs)
        elif name == "image_processor_service":
            from ..services.image_processor_service import ImageProcessorService
            val = ImageProcessorService(self._api_key, self.professor_name, **self._svc_kwargs)
        elif name == "image_translation_service":
            from ..services.image_translation_service import ImageTranslationService
            val = ImageTranslationService(self._api_key, self.professor_name, **self._svc_kwargs)
        elif name == "transcription_review_service":
            from ..services.transcription_review_service import TranscriptionReviewService
            val = TranscriptionReviewService(self._api_key, self.professor_name, **self._svc_kwargs)
        else:
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")
        # Cache on the instance so __getattr__ is only called once per service.
        object.__setattr__(self, name, val)
        return val

    def process_prompt(
        self,
        user_prompt: str,
        system_prompt: Optional[str] = None,
        output_file: Optional[str] = None,
    ) -> None:
        """Send a custom prompt and print (and optionally save) the response."""
        try:
            response = self.prompt_service.send_prompt(user_prompt, system_prompt)
            print("\n" + response)
            if output_file:
                FileOutputHandler.save_to_text_file(response, output_file, label="Response")
        except Exception as e:
            logger.error(f"Error sending prompt: {e}", exc_info=True)
            raise CLIError(f"Error sending prompt: {e}") from e

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
