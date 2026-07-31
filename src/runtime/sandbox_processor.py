"""Wires up the AI services a plugin needs and gives it plain access to them.

``SandboxProcessor`` is the object every plugin builds inside its ``run()``
method. It looks up the professor's API key, sets up token tracking, and
prepares the file-handling and processing tools most plugins need
(``file_output``, ``image_processor``, ``pdf_processor``). Any additional
service or orchestration method a specific plugin needs (e.g. a translation
or transcription service) is supplied by that plugin itself and attached
automatically — see ``__getattr__`` and ``_discover_plugin_mixins`` below for
how that attachment works.
"""

import logging
import sys
from typing import TYPE_CHECKING, Optional, TypedDict

from ..config import get_api_key
from ..errors import CLIError
from ..output.file_output import FileOutputHandler
from ..processors.image_processor import ImageProcessor
from ..processors.pdf_processor import PDFProcessor
from ..tracking.token_tracker import TokenTracker
from .command_runner import _CommandMixin
from .file_types import _FileTypeMixin

if TYPE_CHECKING:
    from ..services.api_config import APIConfig

logger = logging.getLogger(__name__)


class _SvcKwargs(TypedDict, total=False):
    """The settings shared by every AI service ``SandboxProcessor`` creates."""
    token_tracker: TokenTracker
    model: Optional[str]
    temperature: Optional[float]
    top_p: Optional[float]
    max_tokens: Optional[int]


def _discover_plugin_mixins() -> tuple[type, ...]:
    """Find every set of plugin-added capabilities that should be built into SandboxProcessor.

    Each installed plugin can extend what ``SandboxProcessor`` is able to do
    (e.g. adding document-translation or image-transcription methods) by
    registering its own module under the name ``"src.runtime.<plugin_name>"``
    and exporting a class named ``Mixin`` from it — the same registration
    approach already used for plugin-owned services (see ``__getattr__``
    below). This function looks through every currently-loaded module for
    one registered that way and collects its ``Mixin`` class, so that
    ``SandboxProcessor`` can build those capabilities in as if they were
    written directly into this file.

    This only runs once, the first time this module is imported — which is
    safe because ``SandboxProcessor`` is always constructed from inside a
    plugin's ``run()`` method (never at startup), and every plugin has
    already finished registering its modules (via ``load_plugins()``) before
    any ``run()`` method executes. If no plugin has registered any
    capabilities, this returns an empty result and ``SandboxProcessor`` still
    works — it just won't have any mode-specific methods available.

    Returns:
        The collection of plugin-provided classes to combine into
        ``SandboxProcessor``, sorted by module name so the combination order
        is always the same from run to run.
    """
    mixins: list[type] = []
    for module_name, module in list(sys.modules.items()):
        if module is None or not module_name.startswith("src.runtime."):
            continue
        mixin_cls = getattr(module, "Mixin", None)
        if isinstance(mixin_cls, type):
            mixins.append(mixin_cls)
    # Sorted by module name so the combined class is built the same way every
    # time, regardless of the order plugins happened to load in.
    mixins.sort(key=lambda cls: cls.__module__)
    return tuple(mixins)


class SandboxProcessor(*_discover_plugin_mixins(), _FileTypeMixin, _CommandMixin):
    """Central coordinator that a plugin's run() method builds to carry out its command.

    Combines whatever mode-specific capabilities the installed plugins have
    registered (e.g. document translation, image transcription) with the
    always-present file-type detection and interactive-prompt helpers every
    plugin can use, regardless of which plugin is running.
    """

    def __init__(self, professor_name: str, model: Optional[str] = None,
                 temperature: Optional[float] = None, top_p: Optional[float] = None,
                 max_tokens: Optional[int] = None,
                 api_config: Optional["APIConfig"] = None):
        """Set up a processor for one professor's request.

        Args:
            professor_name: The professor's identifier (e.g. ``'heller'``),
                             used to look up their API key and to record
                             token usage under their name.
            model: A specific model to use instead of the plugin's default
                   (e.g. ``'gpt-4o'``). Can also use the colon syntax
                   ``'api_name:model_name'`` (e.g. ``'della:qwen-preview'``)
                   to route requests to an alternate API endpoint — the
                   ``api_name`` portion is parsed automatically and used to
                   load the matching endpoint configuration. ``None`` uses
                   the plugin's own default model.
            temperature: How varied or creative the response should be
                         (``0.0``–``2.0``). ``None`` means the service's
                         own setting is used — never the model's own
                         preference, since a value is always sent.
                         ``None`` uses the default.
            top_p: An alternative response-variety control (``0.0``–``1.0``),
                   ``None`` uses the service's own setting.
            max_tokens: The maximum response length, in tokens (tokens are
                        the unit AI providers use to measure text length,
                        roughly one token per word), overriding the model's
                        default. ``None`` uses the default.
            api_config: An explicit alternate-endpoint configuration to use
                        instead of the sandbox's normal endpoint. ``None``
                        with a colon-syntax ``model`` resolves this
                        automatically; ``None`` with a plain model name uses
                        the standard Princeton AI Sandbox endpoint.

        Raises:
            CLIError: If the professor's configuration is missing or invalid
                      (e.g. no API key set up for them), or if an alternate
                      endpoint named in ``model`` doesn't have a valid
                      configuration.
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
        """Create a plugin-provided service the first time it's accessed by name.

        This is what makes attributes like ``self.translation_service`` work
        even though ``SandboxProcessor`` never defines them directly: when a
        plugin registers a module in ``sys.modules`` under the key
        ``src.services.<name>`` and that module contains a class whose name
        matches ``<name>`` written in capitalized-no-underscores form (e.g.
        the module key ``translation_service`` maps to a class named
        ``TranslationService``), that class is instantiated automatically the
        first time something asks for ``self.<name>``. No changes to this
        file are needed to support a new plugin's services.

        When ``self._api_config`` names an alternate API endpoint (rather
        than the standard Princeton AI Sandbox endpoint), the newly created
        service's network client is replaced with one pointed at that
        endpoint's address, and the model name is used exactly as given
        instead of being checked against the model price catalog — this
        allows external, non-catalog model names to work without extra
        configuration.

        Args:
            name: The attribute name that was accessed but not found on the
                  instance directly, e.g. ``'translation_service'``.

        Returns:
            The newly created service instance, which is also saved on the
            object so future accesses skip this lookup entirely.

        Raises:
            AttributeError: If no plugin has registered a service under this
                             name, or if the registered module doesn't
                             contain a matching class.
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

        # When routing somewhere other than the sandbox, the service is told so
        # and settles the rest itself — the connection, the name its usage is
        # recorded under, and where its model name comes from. This used to be
        # done by reaching in afterwards and replacing the client and the
        # _get_model method, which is how the endpoint's own settings came to be
        # read and ignored and how its usage came to be recorded as though the
        # sandbox had answered it.
        api_config = object.__getattribute__(self, "_api_config")
        if api_config is not None and hasattr(val, "use_endpoint"):
            val.use_endpoint(api_config)

        # Cache on the instance so __getattr__ is only called once per service.
        object.__setattr__(self, name, val)
        return val
