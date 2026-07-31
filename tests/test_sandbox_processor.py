"""Tests for src/runtime/sandbox_processor.py — SandboxProcessor core behavior.

Covers only what's genuinely core: __init__, the __getattr__ lazy service
loader, and _detect_and_validate_file (from _FileTypeMixin). Document/image
handling tests live with their owning plugins:
- plugins/translation/tests/test_document_handler.py
- plugins/transcription/tests/test_image_handler.py
"""

from unittest.mock import MagicMock

import pytest

from src.errors import CLIError
from src.runtime.sandbox_processor import SandboxProcessor


# ---------------------------------------------------------------------------
# Helpers — build a SandboxProcessor bypassing real service init
# ---------------------------------------------------------------------------

def _make_processor(monkeypatch) -> SandboxProcessor:
    """Create a SandboxProcessor with all real services replaced by MagicMocks."""
    monkeypatch.setattr("src.runtime.sandbox_processor.get_api_key",
                        lambda name: ("fake-key", "Professor Fake"))

    # Prevent catalog I/O in service constructors
    for svc_path in (
        "src.services.base_service.resolve_model",
        "src.services.base_service.maybe_sync_model_pricing",
        "src.services.base_service.get_model_system_role",
        "src.services.base_service.get_model_max_completion_tokens",
    ):
        monkeypatch.setattr(svc_path, MagicMock(return_value="gpt-4o"), raising=False)

    # Prevent token tracker from touching disk
    monkeypatch.setattr(
        "src.tracking.token_tracker.TokenTracker.__init__",
        lambda self, professor: None,
    )

    proc = SandboxProcessor.__new__(SandboxProcessor)
    proc.professor_name = "fake"
    proc.professor_display_name = "Professor Fake"
    proc.token_tracker = MagicMock()
    proc.token_tracker.usage_data = {"total_usage": {"total_tokens": 0, "total_cost": 0.0}}
    proc.image_processor = MagicMock()
    proc.pdf_processor = MagicMock()
    proc.file_output = MagicMock()
    return proc


# ---------------------------------------------------------------------------
# SandboxProcessor.__init__
# ---------------------------------------------------------------------------

class TestSandboxProcessorInit:

    def test_init_sets_professor_name(self, monkeypatch, tmp_path):
        monkeypatch.setattr("src.runtime.sandbox_processor.get_api_key",
                            lambda name: ("fake-key", "Dr. Smith"))
        monkeypatch.setattr("src.runtime.sandbox_processor.PromptService",
                            MagicMock(return_value=MagicMock()), raising=False)
        monkeypatch.setattr("src.runtime.sandbox_processor.TokenTracker",
                            MagicMock(return_value=MagicMock()))

        proc = SandboxProcessor("smith")
        assert proc.professor_name == "smith"
        assert proc.professor_display_name == "Dr. Smith"

    def test_init_raises_cli_error_on_bad_config(self, monkeypatch):
        monkeypatch.setattr("src.runtime.sandbox_processor.get_api_key",
                            lambda name: (_ for _ in ()).throw(ValueError("unknown professor")))
        with pytest.raises(CLIError, match="Configuration error"):
            SandboxProcessor("nobody")

    def test_colon_model_parsed_into_api_config(self, monkeypatch):
        """Colon syntax in model string auto-resolves APIConfig without caller involvement."""
        from src.services.api_config import APIConfig
        fake_cfg = APIConfig(
            api_name="hpc_cluster",
            display_name="HPC Cluster",
            base_url="https://cluster.example.com/v1",
            api_key="key",
            openai_compatible=True,
            default_model=None,
        )
        monkeypatch.setattr("src.runtime.sandbox_processor.get_api_key",
                            lambda name: ("fake-key", "Dr. Smith"))
        monkeypatch.setattr("src.runtime.sandbox_processor.TokenTracker",
                            MagicMock(return_value=MagicMock()))
        monkeypatch.setattr(
            "src.runtime.sandbox_processor.SandboxProcessor.__init__.__code__",
            SandboxProcessor.__init__.__code__,
            raising=False,
        )
        # Patch load_api_config inside the sandbox_processor module namespace
        import src.services.api_config as _cfg_mod
        original_load = _cfg_mod.load_api_config

        def fake_load(name):
            if name == "hpc_cluster":
                return fake_cfg
            return original_load(name)

        monkeypatch.setattr(_cfg_mod, "load_api_config", fake_load)

        proc = SandboxProcessor("smith", model="hpc_cluster:llama-3-70b")
        assert proc._api_config is fake_cfg
        assert proc._svc_kwargs["model"] == "llama-3-70b"

    def test_bare_model_leaves_api_config_none_when_no_default(self, monkeypatch):
        """Model without colon and no apis.default → _api_config stays None."""
        monkeypatch.setattr("src.runtime.sandbox_processor.get_api_key",
                            lambda name: ("fake-key", "Dr. Smith"))
        monkeypatch.setattr("src.runtime.sandbox_processor.TokenTracker",
                            MagicMock(return_value=MagicMock()))
        import src.services.api_config as _cfg_mod
        monkeypatch.setattr(_cfg_mod, "get_default_api_name", lambda: None)

        proc = SandboxProcessor("smith", model="gpt-4o")
        assert proc._api_config is None
        assert proc._svc_kwargs["model"] == "gpt-4o"


# ---------------------------------------------------------------------------
# _detect_and_validate_file (from _FileTypeMixin, always present on core)
# ---------------------------------------------------------------------------

class TestDetectAndValidateFile:

    def test_pdf_detected(self, tmp_path, monkeypatch):
        proc = _make_processor(monkeypatch)
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"%PDF-1.4")
        proc.image_processor.is_image_file.return_value = False
        result = proc._detect_and_validate_file(str(f))
        assert result == "pdf"

    def test_docx_detected(self, tmp_path, monkeypatch):
        proc = _make_processor(monkeypatch)
        f = tmp_path / "doc.docx"
        f.write_bytes(b"fake-docx")
        proc.image_processor.is_image_file.return_value = False
        result = proc._detect_and_validate_file(str(f))
        assert result == "docx"

    def test_txt_detected(self, tmp_path, monkeypatch):
        proc = _make_processor(monkeypatch)
        f = tmp_path / "file.txt"
        f.write_text("hello")
        proc.image_processor.is_image_file.return_value = False
        result = proc._detect_and_validate_file(str(f))
        assert result == "txt"

    def test_image_detected(self, tmp_path, monkeypatch):
        proc = _make_processor(monkeypatch)
        f = tmp_path / "scan.jpg"
        f.write_bytes(b"fake-jpg")
        proc.image_processor.is_image_file.return_value = True
        proc.image_processor.validate_image_file.return_value = True
        result = proc._detect_and_validate_file(str(f))
        assert result == "image"

    def test_invalid_image_raises_cli_error(self, tmp_path, monkeypatch):
        proc = _make_processor(monkeypatch)
        f = tmp_path / "bad.jpg"
        f.write_bytes(b"garbage")
        proc.image_processor.is_image_file.return_value = True
        proc.image_processor.validate_image_file.return_value = False
        with pytest.raises(CLIError, match="not valid"):
            proc._detect_and_validate_file(str(f))

    def test_nonexistent_file_raises_cli_error(self, monkeypatch):
        proc = _make_processor(monkeypatch)
        with pytest.raises(CLIError, match="not found"):
            proc._detect_and_validate_file("/no/such/file.txt")

    def test_unsupported_extension_raises_cli_error(self, tmp_path, monkeypatch):
        proc = _make_processor(monkeypatch)
        f = tmp_path / "archive.zip"
        f.write_bytes(b"PK")
        proc.image_processor.is_image_file.return_value = False
        with pytest.raises(CLIError, match="Unsupported"):
            proc._detect_and_validate_file(str(f))


# ---------------------------------------------------------------------------
# SandboxProcessor.__getattr__ — lazy service loader
# ---------------------------------------------------------------------------

class TestSandboxProcessorGetattr:

    def _make_bare_proc(self):
        """Return a SandboxProcessor bypassing __init__, with only the bare minimum set."""
        proc = SandboxProcessor.__new__(SandboxProcessor)
        proc._api_key = "fake-key"
        proc.professor_name = "test"
        proc._svc_kwargs = {}
        proc._api_config = None
        return proc

    def test_raises_attribute_error_when_no_module_in_sys_modules(self):
        import sys
        proc = self._make_bare_proc()
        # Ensure the key is absent
        sys.modules.pop("src.services.nonexistent_service", None)
        with pytest.raises(AttributeError, match="has no attribute 'nonexistent_service'"):
            _ = proc.nonexistent_service

    def test_raises_attribute_error_when_class_missing_from_module(self):
        import sys
        import types
        proc = self._make_bare_proc()
        fake_mod = types.ModuleType("src.services.no_class_service")
        # Module exists but has no matching class
        sys.modules["src.services.no_class_service"] = fake_mod
        try:
            with pytest.raises(AttributeError, match="has no class 'NoClassService'"):
                _ = proc.no_class_service
        finally:
            sys.modules.pop("src.services.no_class_service", None)

    def test_instantiates_class_from_module(self):
        import sys
        import types
        proc = self._make_bare_proc()
        fake_mod = types.ModuleType("src.services.my_service")
        instance = MagicMock()
        fake_cls = MagicMock(return_value=instance)
        fake_mod.MyService = fake_cls
        sys.modules["src.services.my_service"] = fake_mod
        try:
            result = proc.my_service
            assert result is instance
            fake_cls.assert_called_once_with("fake-key", "test")
        finally:
            sys.modules.pop("src.services.my_service", None)

    def test_result_is_cached_on_instance(self):
        import sys
        import types
        proc = self._make_bare_proc()
        fake_mod = types.ModuleType("src.services.cached_service")
        instance = MagicMock()
        fake_cls = MagicMock(return_value=instance)
        fake_mod.CachedService = fake_cls
        sys.modules["src.services.cached_service"] = fake_mod
        try:
            first = proc.cached_service
            second = proc.cached_service
            assert first is second
            # Class constructor called only once
            assert fake_cls.call_count == 1
        finally:
            sys.modules.pop("src.services.cached_service", None)


class TestServicesKnowWhichEndpointTheyAreOn:
    """A service pointed at an alternate endpoint has to say so when recording.

    Its network client is swapped after it is built, so nothing about the
    service itself changes — which is why usage from an alternate endpoint used
    to be recorded as though the sandbox had answered it, and costed against the
    university's price list.
    """

    def _service_from(self, monkeypatch, api_config):
        import sys
        import types

        from src.services.base_service import BaseService

        class Chatter(BaseService):
            def __init__(self, *args, **kwargs):
                self.client = object()

        module = types.ModuleType("src.services.chatter")
        module.Chatter = Chatter
        monkeypatch.setitem(sys.modules, "src.services.chatter", module)

        proc = SandboxProcessor.__new__(SandboxProcessor)
        object.__setattr__(proc, "_api_key", "k")
        object.__setattr__(proc, "professor_name", "Dr. Smith")
        object.__setattr__(proc, "_svc_kwargs", {})
        object.__setattr__(proc, "_api_config", api_config)
        return proc.__getattr__("chatter")

    def test_a_service_on_an_endpoint_is_told_its_name(self, monkeypatch):
        from src.services.api_config import APIConfig

        service = self._service_from(monkeypatch, APIConfig(
            api_name="hpc_cluster", display_name="HPC Cluster",
            base_url="https://cluster.example.com/v1", api_key="key",
        ))
        assert service.endpoint_name == "hpc_cluster"

    def test_an_endpoint_that_does_not_speak_openai_is_refused_plainly(self, monkeypatch):
        """The setting was read and ignored, so it looked like it took effect."""
        import pytest

        from src.errors import CLIError
        from src.services.api_config import APIConfig

        with pytest.raises(CLIError, match="OpenAI-compatible"):
            self._service_from(monkeypatch, APIConfig(
                api_name="odd_one", display_name="Odd", base_url="https://x/v1",
                api_key="k", openai_compatible=False,
            ))

    def test_an_endpoint_that_says_nothing_about_it_still_works(self, monkeypatch):
        """Omitting the setting used to work, and must go on working."""
        from src.services.api_config import APIConfig

        service = self._service_from(monkeypatch, APIConfig(
            api_name="quiet", display_name="Quiet", base_url="https://x/v1", api_key="k",
        ))
        assert service.endpoint_name == "quiet"

    def test_a_service_on_the_sandbox_names_no_endpoint(self, monkeypatch):
        service = self._service_from(monkeypatch, None)
        assert service.endpoint_name == ""


class TestAnEndpointsOwnSettingsAreUsed:
    """Settings that were parsed and then read by nothing.

    A setting that is read and ignored is worse than one that does not exist:
    it looks like it took effect.
    """

    def _service(self, monkeypatch, **config):
        import sys
        import types

        from src.services.api_config import APIConfig
        from src.services.base_service import BaseService

        class Chatter(BaseService):
            def __init__(self, *args, **kwargs):
                # Stands in for the real one, which does the same two things
                # among others. Skipping super() avoids building a Portkey
                # client and a token tracker for a test about neither.
                self.client = object()
                self.custom_model = None

        module = types.ModuleType("src.services.chatter")
        module.Chatter = Chatter
        monkeypatch.setitem(sys.modules, "src.services.chatter", module)

        cfg = APIConfig(
            api_name="cluster", display_name="Cluster",
            base_url="https://cluster.example.com/v1", api_key="key", **config,
        )
        proc = SandboxProcessor.__new__(SandboxProcessor)
        object.__setattr__(proc, "_api_key", "k")
        object.__setattr__(proc, "professor_name", "Dr. Smith")
        object.__setattr__(proc, "_svc_kwargs", {})
        object.__setattr__(proc, "_api_config", cfg)
        return proc.__getattr__("chatter")

    def test_the_endpoints_address_is_the_one_used(self, monkeypatch):
        service = self._service(monkeypatch)
        assert "cluster.example.com" in str(service.client.base_url)

    def test_its_timeout_is_the_one_used(self, monkeypatch):
        service = self._service(monkeypatch, timeout=7)
        assert service.client.timeout == 7.0

    def test_turning_off_certificate_checking_actually_turns_it_off(self, monkeypatch):
        """Parsed since it was added, and until now acted on by nothing."""
        service = self._service(monkeypatch, verify_ssl=False)
        transport = service.client._client._transport
        assert transport._pool._ssl_context.verify_mode.name == "CERT_NONE"

    def test_leaving_it_on_leaves_it_on(self, monkeypatch):
        service = self._service(monkeypatch, verify_ssl=True)
        transport = service.client._client._transport
        assert transport._pool._ssl_context.verify_mode.name != "CERT_NONE"

    def test_it_says_so_when_certificate_checking_is_off(self, monkeypatch, caplog):
        """A real weakening should not happen quietly."""
        import logging

        with caplog.at_level(logging.WARNING):
            self._service(monkeypatch, verify_ssl=False)
        assert "Certificate checking is turned off" in caplog.text

    def test_the_model_is_taken_as_given_not_looked_up(self, monkeypatch):
        """The catalogue describes the sandbox's models, not this endpoint's."""
        service = self._service(monkeypatch, default_model="llama-3-70b")
        assert service._get_model() == "llama-3-70b"

    def test_a_model_asked_for_beats_the_endpoints_own_default(self, monkeypatch):
        service = self._service(monkeypatch, default_model="llama-3-70b")
        service.custom_model = "mistral-large"
        assert service._get_model() == "mistral-large"

    def test_no_model_anywhere_says_so_plainly(self, monkeypatch):
        import pytest

        from src.errors import CLIError

        service = self._service(monkeypatch)
        with pytest.raises(CLIError, match="No model was named"):
            service._get_model()
