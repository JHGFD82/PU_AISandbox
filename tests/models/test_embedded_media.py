"""Tests for src/models/embedded_media.py: a picture taken from a document, and where it sat."""

from src.models.embedded_media import EmbeddedMedia


# ---------------------------------------------------------------------------
# EmbeddedMedia dataclass
# ---------------------------------------------------------------------------

class TestEmbeddedMedia:

    def test_required_fields(self):
        item = EmbeddedMedia(data=b"img", content_type="image/png", position_fraction=0.5)
        assert item.data == b"img"
        assert item.content_type == "image/png"
        assert item.position_fraction == 0.5

    def test_optional_emu_fields_default_to_none(self):
        item = EmbeddedMedia(data=b"img", content_type="image/png", position_fraction=0.0)
        assert item.width_emu is None
        assert item.height_emu is None

    def test_emu_fields_stored_when_provided(self):
        item = EmbeddedMedia(data=b"img", content_type="image/jpeg", position_fraction=0.25,
                             width_emu=914400, height_emu=457200)
        assert item.width_emu == 914400
        assert item.height_emu == 457200

    def test_position_fraction_at_boundaries(self):
        start = EmbeddedMedia(data=b"", content_type="image/png", position_fraction=0.0)
        end = EmbeddedMedia(data=b"", content_type="image/png", position_fraction=1.0)
        assert start.position_fraction == 0.0
        assert end.position_fraction == 1.0
