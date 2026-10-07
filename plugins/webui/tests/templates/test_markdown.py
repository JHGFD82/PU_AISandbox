"""Tests for plugins/webui/src/templates/_markdown.html: how what the model wrote is drawn on the page."""

from __future__ import annotations

from plugins.webui.tests.helpers import (
    WEBUI_SRC,
)


class TestWhatTheModelWroteIsRendered:
    """Markdown becomes what it means, and never becomes markup."""

    def _source(self, name):

        return (WEBUI_SRC / "templates" / name).read_text()

    def test_the_renderer_is_part_of_the_page(self):
        assert '{% include "_markdown.html" %}' in self._source("chat.html")

    def test_nothing_the_model_wrote_is_ever_treated_as_markup(self):
        """The hard requirement. This text comes from a model, a model reads
        whatever document it was given, and a document can be written by
        anyone — so a translation of somebody's PDF must not be able to put a
        script on the page."""
        import re

        # The code only: the note at the top of the file says the word while
        # promising the opposite, and an earlier version of this test failed on
        # its own documentation.
        renderer = self._source("_markdown.html")
        code = renderer.split("#}", 1)[1]
        code = re.sub(r"//.*", "", code)
        assert not re.search(r"\.innerHTML\s*=", code), "the renderer assigns markup"
        assert "insertAdjacentHTML" not in code
        assert "createTextNode" in code

    def test_only_addresses_that_go_somewhere_become_links(self):
        renderer = self._source("_markdown.html")
        scheme = renderer.split("function safeHref")[1].split("}")[0]
        assert "https?" in scheme and "mailto" in scheme

    def test_the_persons_own_words_are_left_as_they_typed_them(self):
        """Somebody pasting source has every right to asterisks in it."""
        chat = self._source("chat.html")
        assert 'if (m.role === "user")' in chat
        assert ".msg-body.verbatim { white-space: pre-wrap; }" in chat

    def test_a_reply_is_only_rendered_once_it_has_finished_arriving(self):
        """Markdown half-written is markdown half-parsed."""
        chat = self._source("chat.html")
        streaming = chat.split('messageLeaf("assistant", chosenModel')[1][:300]
        assert "verbatim" in streaming

    def test_both_copy_buttons_are_the_same_button(self):
        """They were drawn in two places and drifted: same box, but the
        drawings filled 82% and 62% of it, so one plainly looked smaller."""
        chat = self._source("chat.html")
        renderer = self._source("_markdown.html")
        actions = self._source("_actions.html")
        assert "function copyButtonElement" in actions
        assert "copyButtonElement(" in chat, "the message row draws its own"
        assert "copyButtonElement(" in renderer, "the code block draws its own"
        assert "copyBtn.innerHTML" not in chat

    def test_every_action_icon_is_drawn_on_the_same_grid(self):
        """A drawing filling less of its box looks smaller at the same size."""
        import re

        for name in ("_actions.html", "_markdown.html"):
            source = self._source(name)
            boxes = set(re.findall(r'viewBox="([^"]+)"', source))
            boxes |= set(re.findall(r'setAttribute\("viewBox",\s*"([^"]+)"\)', source))
            assert boxes, f"{name} has no drawings"
            assert boxes == {"0 0 14 14"}, f"{name} draws on {sorted(boxes)}"

    def test_the_copy_button_says_it_worked(self):
        """The thing you pressed answers, rather than a message appearing
        elsewhere for the eye to find."""
        actions = self._source("_actions.html")
        assert "icon-copied" in actions
        assert 'classList.add("copied")' in actions
        assert "2000" in actions, "it never goes back"

    def test_and_only_after_the_copy_actually_happened(self):
        actions = self._source("_actions.html")
        before, _, after = actions.partition("writeText(text).then(")
        assert 'classList.add("copied")' in after
        assert 'classList.add("copied")' not in before

    def test_the_two_drawings_fade_between_each_other(self):
        chat = self._source("chat.html")
        assert ".msg-action-btn.copied .action-icons .icon-copied" in chat
        assert ".action-icons svg" in chat or ".icon-stack svg" in chat

    def test_no_heading_is_smaller_than_the_text_it_introduces(self):
        """A model writing "## Section" produced a 14px line above a 16px
        paragraph — the one thing a heading cannot be."""
        import re

        chat = self._source("chat.html")
        system = self._source("_design-system.html")
        sizes = dict(re.findall(r"(--text-[a-z]+):\s*([0-9.]+)rem", system))
        body = float(sizes["--text-body"])
        for tag in ("h3", "h4", "h5", "h6"):
            rule = chat.split(f".msg-body {tag}.md-heading")[1].split("}")[0]
            token = re.search(r"var\((--text-[a-z]+)\)", rule).group(1)
            assert float(sizes[token]) >= body, (
                f"{tag} is {float(sizes[token]) * 16:.0f}px against body at {body * 16:.0f}px"
            )

    def test_the_headings_get_larger_the_higher_they_are(self):
        import re

        chat = self._source("chat.html")
        sizes = dict(re.findall(r"(--text-[a-z]+):\s*([0-9.]+)rem", self._source("_design-system.html")))
        steps = []
        for tag in ("h3", "h4", "h5", "h6"):
            rule = chat.split(f".msg-body {tag}.md-heading")[1].split("}")[0]
            steps.append(float(sizes[re.search(r"var\((--text-[a-z]+)\)", rule).group(1)]))
        assert steps == sorted(steps, reverse=True), steps

    def test_a_reply_is_set_as_a_document_throughout(self):
        """Headings in the interface's face read as furniture — part of the
        application rather than part of what was written."""
        chat = self._source("chat.html")
        heading = chat.split(".md-heading {")[1].split("}")[0]
        # The reading face, whichever of the two it currently is — so a heading
        # can never be set differently from the passage it introduces.
        assert "var(--font-reading)" in heading
        body = chat.split(".msg-body {")[1].split("}")[0]
        assert "var(--font-reading)" in body
        # And no part of a reply names a face directly, which is how one of them
        # would stop following the choice.
        body_rules = chat.split(".msg-body {")[1].split(".code-block {")[0]
        assert "var(--font-ui)" not in body_rules, "part of a reply is set as interface"
        assert "var(--font-text)" not in body_rules, "part of a reply ignores the choice"

    def test_a_code_block_can_be_taken_away(self):
        renderer = self._source("_markdown.html")
        assert "Copy this code" in renderer
        assert "Save as ${filename}" in renderer

    def test_the_saved_file_is_named_from_the_language(self):
        """A fence says what it is; this is the table that turns that into a
        file name, so a download arrives as parse.py rather than snippet.txt."""
        renderer = self._source("_markdown.html")
        table = renderer.split("CODE_EXTENSIONS = {")[1].split("};")[0]
        for language, extension in (("python", "py"), ("typescript", "ts"),
                                    ("sql", "sql"), ("rust", "rs")):
            assert f'{language}: "{extension}"' in table, language

    def test_a_fence_may_name_its_own_file(self):
        renderer = self._source("_markdown.html")
        assert "function codeFileName" in renderer
        assert "if (named) return named;" in renderer

    def test_code_is_allowed_to_be_wider_than_the_prose(self):
        """Wrapping code at the reading measure would break lines that mean
        something; it scrolls in its own box instead."""
        chat = self._source("chat.html")
        rule = chat.split(".code-block pre {")[1].split("}")[0]
        assert "overflow-x: auto" in rule
