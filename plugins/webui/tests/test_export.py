"""Tests for plugins/webui/src/export.py: saving a conversation as a document someone else can read."""

import sys
from types import SimpleNamespace

import pytest
from docx import Document

export = sys.modules["_pu_webui_export"]


def _message(role, content, *, model=None, cost=None, attachments=()):
    return SimpleNamespace(role=role, content=content, timestamp="2026-10-07 10:00",
                           model=model, cost=cost, attachments=list(attachments))


@pytest.fixture
def conversation():
    return SimpleNamespace(
        title="Letters of 1831",
        model="gpt-4o",
        messages=[
            _message("user", "Summarise the letter.",
                     attachments=[SimpleNamespace(filename="letter.pdf", char_count=12345)]),
            _message("assistant", "It asks for money.", model="gpt-4o", cost=0.0123),
            _message("assistant", "   "),
        ],
    )


class TestTheTranscript:
    def test_it_opens_with_what_the_conversation_was(self, conversation):
        text = export.build_transcript(conversation)
        assert text.startswith("Princeton University AI Sandbox")
        assert "Title: Letters of 1831" in text and "Model: gpt-4o" in text

    def test_each_turn_says_who_spoke_and_when(self, conversation):
        text = export.build_transcript(conversation)
        assert "You — 2026-10-07 10:00\nSummarise the letter." in text

    def test_a_reply_says_which_model_wrote_it_and_what_it_cost(self, conversation):
        assert "Assistant — 2026-10-07 10:00 · gpt-4o · $0.0123" in export.build_transcript(conversation)

    def test_an_attached_document_is_named_with_its_length(self, conversation):
        assert "[Attached: letter.pdf, 12,345 characters]" in export.build_transcript(conversation)

    def test_an_empty_message_is_marked_rather_than_left_blank(self, conversation):
        assert "(empty message)" in export.build_transcript(conversation)


class TestSaving:
    @pytest.mark.parametrize("fmt", ["docx", "pdf", "md", "txt"])
    def test_each_format_writes_a_file(self, conversation, tmp_path, fmt):
        out = tmp_path / f"conversation.{fmt}"
        export.export_conversation(conversation, fmt, str(out))
        assert out.exists() and out.stat().st_size > 0

    def test_the_word_document_holds_the_transcript(self, conversation, tmp_path):
        out = tmp_path / "conversation.docx"
        export.export_conversation(conversation, "docx", str(out))
        text = "\n".join(p.text for p in Document(str(out)).paragraphs)
        assert "It asks for money." in text

    @pytest.mark.parametrize("fmt", ["md", "txt"])
    def test_the_text_formats_hold_the_transcript(self, conversation, tmp_path, fmt):
        out = tmp_path / f"conversation.{fmt}"
        export.export_conversation(conversation, fmt, str(out))
        assert "It asks for money." in out.read_text(encoding="utf-8")

    def test_a_format_it_does_not_write_is_refused_by_name(self, conversation, tmp_path):
        with pytest.raises(export.ExportError, match="'odt'.*docx, md, pdf, txt"):
            export.export_conversation(conversation, "odt", str(tmp_path / "x.odt"))
