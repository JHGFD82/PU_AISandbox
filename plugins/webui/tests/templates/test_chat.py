"""Tests for plugins/webui/src/templates/chat.html: the chat page as the browser receives it."""

from __future__ import annotations

import re
import sys

import pytest

from plugins.webui.tests.helpers import (
    WEBUI_SRC,
    _rendered_chat,
)

# Every test here runs as two fake professors with no passphrase set; see conftest.py.
pytestmark = pytest.mark.usefixtures("_configured_professors", "_no_passphrase")


class TestFileOrFolderPicker:
    """A file field that accepts folders must still accept a single file.

    `webkitdirectory` does not add folder selection, it replaces file selection:
    an input carrying it can only pick a directory. Setting it unconditionally
    is what stopped anyone choosing one document to translate, so the page must
    offer the choice instead of assuming.
    """

    def _page(self):

        from fastapi.templating import Jinja2Templates

        directory = WEBUI_SRC / "templates"
        return Jinja2Templates(directory=str(directory)).get_template("chat.html").render(request=None)

    def test_the_page_offers_both_modes(self):
        page = self._page()
        assert "A single file" in page
        assert "A whole folder" in page

    def test_webkitdirectory_is_never_set_unconditionally(self):
        """The regression itself: set outside a mode choice, folders are all you get."""
        import re

        page = self._page()
        for match in re.finditer(r"webkitdirectory = true", page):
            before = page[max(0, match.start() - 400):match.start()]
            assert 'mode === "folder"' in before, (
                "webkitdirectory must only be set for the folder mode; setting it "
                "on every file field is what removed single-file selection"
            )

    def test_the_choice_defaults_to_a_single_file(self):
        """The commoner case, and the one that broke."""
        page = self._page()
        assert "radio.checked = index === 0" in page
        # The modes are built up rather than written as one literal now, so that
        # a field can offer folders, pasted text, both or neither — but a single
        # file is always the first, and therefore the default.
        assert 'const modes = [["file", "A single file"]];' in page

    def test_the_rebuilt_input_keeps_the_field_id(self):
        """collect, restore and submit all look the field up by this id."""
        page = self._page()
        assert "replacement.id = input.id" in page


class TestConversationsAreGroupedByAge:
    """A long list is easier to find your way around when it is dated."""

    def _page(self):

        return (WEBUI_SRC / "templates" / "chat.html").read_text()

    def test_the_page_groups_by_how_recent_a_conversation_is(self):
        page = self._page()
        for group in ("Today", "This week", "This month", "Older"):
            assert f'"{group}"' in page

    def test_a_heading_goes_in_only_where_the_group_changes(self):
        """Otherwise every conversation gets one — and only while the list is
        in an order by age, since sorted by cost the groups would be split up
        into dozens of one-line pieces."""
        page = self._page()
        assert "if (dated && group !== currentGroup) {" in page
        assert 'const dated = state.sort === "newest" || state.sort === "oldest";' in page

    def test_the_headings_stay_in_view_while_scrolling(self):
        page = self._page()
        block = page.split(".conv-group {")[1].split("}")[0]
        assert "position: sticky" in block

    def test_every_heading_is_the_same_height(self):
        """They are sticky, so two are seen together as one passes the other.

        The first one used to be trimmed to save a little space at the top of
        the list, which made the pair jump as they scrolled past each other.
        """
        import re

        css = self._page().split("</style>")[0]
        rules = re.findall(r"([^{}]*\.conv-group[^{}]*)\{([^}]*)\}", css)
        sizing = ("padding", "height", "margin", "font-size", "line-height")
        assert len(rules) == 1, (
            "more than one rule sets a conversation heading's box: "
            f"{[r[0].strip().splitlines()[-1] for r in rules]}"
        )
        assert not any(
            key in rules[0][1] for key in sizing if ":first-child" in rules[0][0]
        )

    def test_the_server_still_decides_the_order(self, unlocked_client):
        """Grouping is a heading over an order it does not change, and newest
        first — what the list shows until someone picks another order — is
        the server's own, passed through untouched."""
        page = self._page()
        assert "state.conversations = data.conversations;" in page
        assert "conversations.sort" not in page
        sorter = page.split("function sortedConversations(list) {")[1].split("\n}\n")[0]
        assert "default: return list;" in sorter


class TestTheSandboxsMarkSitsBehindItsName:
    def _page(self):

        from fastapi.templating import Jinja2Templates

        here = WEBUI_SRC / "templates"
        return (Jinja2Templates(directory=str(here)).env
                .get_template("chat.html").render(request=None))

    def test_the_title_is_the_sandboxs_name(self):
        page = self._page()
        assert "Princeton University AI Sandbox" in page
        assert "Chat UI" not in page

    def _data_uri(self):
        import re

        found = re.search(r'url\("data:image/svg\+xml,(.*?)"\)', self._page(), re.S)
        assert found, "no mark is embedded"
        return found.group(1)

    def test_the_mark_is_a_real_drawing_and_not_a_broken_link(self):
        """It is written into the page, so nothing fetches it and finds it gone."""
        import xml.etree.ElementTree as ET
        from urllib.parse import unquote

        root = ET.fromstring(unquote(self._data_uri()))
        assert root.tag.endswith("svg")
        assert len(root) == 3, "the mark is missing part of its drawing"

    def test_a_browser_reads_the_whole_address(self):
        """A '#' inside it would end the address and drop the rest of the drawing.

        The colour of every path in this mark is written as a '#' followed by
        six digits, so this is not a hypothetical: unencoded, a browser stops
        reading part-way through the first path and draws nothing at all. It
        cost an afternoon once, and reading the string back in Python does not
        show it, because Python does not stop at a '#'.
        """
        import xml.etree.ElementTree as ET
        from urllib.parse import unquote

        uri = self._data_uri()
        assert "#" not in uri, "the address ends early at a '#' and the mark will not draw"

        # And what a browser would actually be handed is still a whole drawing.
        as_a_browser_reads_it = uri.split("#")[0]
        root = ET.fromstring(unquote(as_a_browser_reads_it))
        assert len(root) == 3

    def test_the_mark_is_actually_drawn(self):
        page = self._page()
        mark = page.split(".page-title-mark {")[1].split("}")[0]
        assert float(mark.split("opacity:")[1].split(";")[0]) > 0

    def test_the_name_does_not_sit_on_top_of_the_mark(self):
        """The mark is drawn at full strength, so the words move clear of it
        rather than being read through it."""
        page = self._page()
        text = page.split(".page-title-text {")[1].split("}")[0]
        assert "left:" in text, "the name would overlap the mark it is meant to sit beside"

    def test_it_is_visible_in_both_themes(self):
        page = self._page()
        assert '[data-theme="dark"] .page-title-mark' in page

    def test_a_screen_reader_is_not_told_the_name_twice(self):
        page = self._page()
        assert '<span class="page-title-mark" aria-hidden="true">' in page


class TestTheSuppliedButtonIcons:
    """Drawings supplied for the buttons, fitted to how the buttons work here."""

    def _button(self, button_id):

        # Rendered, not read: the icons used in more than one place are macro
        # calls in the source now, and it is the drawing that reaches the
        # browser that these tests are about.
        page = _rendered_chat()
        start = page.index(f'id="{button_id}"')
        return page[page.rindex("<button", 0, start): page.index("</button>", start)]

    def test_every_icon_takes_its_colour_from_the_button(self):
        """They were supplied painted white, which is invisible on a light page."""

        for button_id in ("theme-toggle-btn", "quit-btn", "sampling-options-btn",
                          "plugin-action-btn", "settings-btn", "spend-toggle-btn",
                          "model-toggle-btn", "model-add-btn", "job-modal-reset",
                          "settings-modal-close", "job-modal-close"):
            block = self._button(button_id)
            assert "currentColor" in block, button_id
            assert 'fill="white"' not in block, f"{button_id} is painted white regardless of theme"
            assert "fill-opacity" not in block, f"{button_id} is drawn faded"

    def test_the_theme_button_still_holds_both_drawings(self):
        """It cross-fades between them; one would leave nothing to fade to."""
        import re

        block = self._button("theme-toggle-btn")
        assert len(re.findall(r"<svg\b", block)) == 2
        assert 'class="icon-sun"' in block and 'class="icon-moon"' in block

    def test_each_drawing_is_whole(self):
        """A path lost in the swap would show as a piece of an icon."""
        import re
        import xml.etree.ElementTree as ET

        expected = {"quit-btn": 1, "sampling-options-btn": 1, "plugin-action-btn": 1}
        for button_id, paths in expected.items():
            root = ET.fromstring(re.search(r"<svg\b.*?</svg>", self._button(button_id), re.S).group(0))
            assert len(root) == paths, button_id

    def test_every_button_uses_the_supplied_artwork(self):
        """None left on the drawing it shipped with."""
        import re

        # Rendered, not read: the icons used in more than one place are macro
        # calls in the source now, and it is the drawing that reaches the
        # browser that these tests are about.
        page = _rendered_chat()
        # Buttons with no supplied drawing to use. The artwork for a "sidebar
        # toggle" was a second copy of the padlock, so that button — which only
        # appears on a narrow screen — is still on a plain one. Nothing was
        # supplied for a folder or a download either, so those two are drawn,
        # in the same stroked style as the download arrow on a single message,
        # and nor for the funnel that filters the conversation list.
        awaiting_artwork = {"sidebar-toggle-btn", "conv-bar-folder",
                            "conv-bar-download-btn", "conv-filter"}
        for m in re.finditer(r'<button\b[^>]*id="([^"]+)"[^>]*>(.*?)</button>', page, re.S):
            block = m.group(2)
            if "<svg" not in block or m.group(1) in awaiting_artwork:
                continue
            # The originals were drawn as strokes; every supplied one is a
            # filled shape, so a leftover would show up here.
            assert 'stroke="currentColor"' not in block, f"{m.group(1)} is still the old drawing"

    def test_the_conversation_menu_dots_stand_upright(self):
        """They are supplied in a row, and the button wants a column."""
        import re

        # Rendered, not read: the icons used in more than one place are macro
        # calls in the source now, and it is the drawing that reaches the
        # browser that these tests are about.
        page = _rendered_chat()
        dots = re.search(r"menuBtn\.innerHTML = '(.*?)';", page, re.S).group(1)
        assert "icon-upright" in dots
        assert ".icon-upright { transform: rotate(90deg); }" in page

    def test_the_menu_button_gives_those_dots_a_square_to_sit_in(self):
        """A drawing that is turned upright cannot be fitted to its old shape.

        The box was 3px by 13px, for a drawing that was already vertical. The
        supplied one is a wide row: fitted to that box it would come out 3px by
        0.6px, and rotating that would not help.
        """

        # Rendered, not read: the icons used in more than one place are macro
        # calls in the source now, and it is the drawing that reaches the
        # browser that these tests are about.
        page = _rendered_chat()
        rule = page.split(".conv-menu-btn svg {")[1].split("}")[0]
        width = rule.split("width:")[1].split("px")[0].strip()
        height = rule.split("height:")[1].split("px")[0].strip()
        assert width == height, f"the dots would be squashed before being turned: {width}x{height}"

    def test_a_cross_is_the_plus_turned(self):
        """Asked for: the same drawing, rotated, rather than a second one."""

        # Rendered, not read: the icons used in more than one place are macro
        # calls in the source now, and it is the drawing that reaches the
        # browser that these tests are about.
        page = _rendered_chat()
        for button_id in ("settings-modal-close", "job-modal-close"):
            start = page.index(f'id="{button_id}"')
            block = page[page.rindex("<button", 0, start): page.index("</button>", start)]
            assert "icon-as-cross" in block, button_id
        assert "rotate(45deg)" in page
        # Turned, its corners reach further than its sides did, so it is scaled
        # back to sit level with the icons beside it.
        assert "scale(0.707)" in page

    def test_the_new_conversation_button_reads_like_the_send_button(self):
        """It was asked for in the logo's orange, and drawn that way put an
        orange mark on a button that is itself orange — 1.46:1 under the
        pointer. It takes the button's own colours now, as Send does."""

        # Rendered, not read: the icons used in more than one place are macro
        # calls in the source now, and it is the drawing that reaches the
        # browser that these tests are about.
        page = _rendered_chat()
        start = page.index('id="new-conv"')
        block = page[page.rindex("<button", 0, start): page.index("</button>", start)]
        assert "currentColor" in block
        assert "#f58025" not in block

    def test_no_drawing_carries_a_hidden_backing_rectangle(self):
        """Each was supplied with a fully transparent rect the size of itself."""
        for button_id in ("theme-toggle-btn", "quit-btn", "sampling-options-btn",
                          "plugin-action-btn", "settings-btn", "spend-toggle-btn",
                          "new-conv", "model-toggle-btn", "model-add-btn",
                          "job-modal-reset", "settings-modal-close", "job-modal-close"):
            assert "<rect" not in self._button(button_id), button_id


class TestEveryScriptBlockIsValidJavaScript:
    """An editor reads a script block as JavaScript, whatever the file's
    extension. A Jinja expression sitting in one is a syntax error to it —
    marks against a file that is perfectly correct, which is how people learn
    to ignore the marks."""

    def _templates(self):

        return sorted((WEBUI_SRC / "templates").glob("*.html"))

    def test_no_template_writes_jinja_into_a_script_block(self):
        import re

        offenders = []
        for template in self._templates():
            for block in re.finditer(r"<script(?![^>]*type=)[^>]*>(.*?)</script>",
                                     template.read_text(), re.S):
                for found in re.findall(r"\{\{.*?\}\}|\{%.*?%\}", block.group(1)):
                    offenders.append(f"{template.name}: {found[:60]}")
        assert not offenders, (
            "server values belong in an attribute the script reads, not written "
            "into the script itself — see the note on chat.html's body tag:\n  "
            + "\n  ".join(offenders))

    def _body_attributes(self, page):
        """The attributes a browser would actually find on the body element.

        Asked of a parser rather than searched for in the text: the word
        "<body>" appears in a CSS comment in this file as well, and an
        attribute written there is raw text inside a style block — present in
        the page, invisible to the script, and indistinguishable from the real
        thing to anything that only looks for the string.
        """
        from html.parser import HTMLParser

        class Read(HTMLParser):
            def __init__(self):
                super().__init__()
                self.found = None

            def handle_starttag(self, tag, attrs):
                if tag == "body" and self.found is None:
                    self.found = dict(attrs)

        reader = Read()
        reader.feed(page)
        return reader.found or {}

    def test_what_the_server_settles_arrives_on_the_body_tag(self):
        attributes = self._body_attributes(_rendered_chat())
        assert "data-can-reveal" in attributes
        assert "data-default-sampling" in attributes

    def test_and_the_script_reads_it_from_there(self):
        page = _rendered_chat()
        assert 'document.body.dataset.canReveal === "true"' in page
        assert "JSON.parse(document.body.dataset.defaultSampling" in page

    def test_the_page_has_exactly_one_body_tag(self):
        """A stray one would end the head early and take the stylesheet with
        it. The word appears in a CSS comment too, which is why this asks a
        parser rather than counting the text."""
        from html.parser import HTMLParser

        class Count(HTMLParser):
            def __init__(self):
                super().__init__()
                self.bodies = 0

            def handle_starttag(self, tag, attrs):
                if tag == "body":
                    self.bodies += 1

        counter = Count()
        counter.feed(_rendered_chat())
        assert counter.bodies == 1

    def test_a_missing_value_still_leaves_something_the_script_can_read(self):
        """A route that forgot to pass one must not produce an attribute the
        script then fails to parse."""

        from fastapi.templating import Jinja2Templates

        here = WEBUI_SRC / "templates"
        page = (Jinja2Templates(directory=str(here)).env
                .get_template("chat.html").render(request=None))
        assert 'data-can-reveal="false"' in page
        assert "data-default-sampling='{}'" in page


class TestTheNewConversationButtonReads:
    """It is the one button whose drawing is the same colour as buttons are."""

    def _chat(self):
        return _rendered_chat()

    def test_it_takes_the_ordinary_button_colours(self):
        """Including on hover, so the mark stays readable at the moment it is
        pressed — which is when it used to vanish."""
        chat = self._chat()
        assert ".plus-btn:hover" not in chat, "it opts out of the button hover again"
        rule = chat.split(".plus-btn {")[1].split("}")[0]
        assert "background" not in rule

    def test_the_drawings_own_backing_square_is_gone(self):
        """The button is that square. Two of them made the button look dark."""
        chat = self._chat()
        assert "display: none" in chat.split(".plus-btn .plus-plate {")[1].split("}")[0]

    def test_the_plate_is_named_in_the_drawing(self):
        chat = self._chat()
        start = chat.index('id="new-conv"')
        block = chat[chat.rindex("<button", 0, start): chat.index("</button>", start)]
        assert 'class="plus-plate"' in block
        assert block.count("<path") == 2, "the mark should still be a plate and a plus"

    def test_the_mark_takes_the_buttons_colour_like_every_other_icon(self):
        """Naming a colour here is what led to a drawing fighting the thing it
        was drawn on."""
        chat = self._chat()
        start = chat.index('id="new-conv"')
        block = chat[chat.rindex("<button", 0, start): chat.index("</button>", start)]
        assert 'fill="currentColor"' in block
        assert "#f58025" not in block


class TestResizingTheJobModal:
    """The splitter between a plugin's options and its prompt preview.

    Some plugins ask three questions and some ask fifteen; the prompt they build
    can be a paragraph or a page. Neither side always deserves the room, so the
    split is left to whoever is looking at it.
    """

    @pytest.fixture
    def chat(self):
        return (WEBUI_SRC / "templates"
                / "chat.html").read_text()

    def test_the_splitter_sits_between_the_two_halves(self, chat):
        body = chat.split('<div class="job-modal-body">')[1].split("</div>\n      <div class=\"job-modal-footer\"")[0]
        assert body.index('id="job-options"') < body.index('id="job-splitter"')
        assert body.index('id="job-splitter"') < body.index('class="job-preview"')

    def test_it_is_announced_as_a_separator_and_can_be_focused(self, chat):
        tag = chat.split('id="job-splitter"')[0].rsplit("<div", 1)[1] + \
              chat.split('id="job-splitter"')[1].split(">")[0]
        assert 'role="separator"' in tag
        assert 'tabindex="0"' in tag

    def test_the_arrow_keys_move_it(self, chat):
        """A control that answers only to a held-down mouse button excludes people."""
        handler = chat.split('splitter.addEventListener("keydown"')[1].split("\n  });")[0]
        for key in ("ArrowLeft", "ArrowRight", "Home", "End"):
            assert key in handler

    def test_the_options_width_is_a_variable_the_splitter_writes(self, chat):
        assert "--job-options-width" in chat
        assert "flex: 0 0 var(--job-options-width)" in chat

    def test_the_preview_can_shrink(self, chat):
        """Without min-width:0 a flex child refuses to go below its content."""
        rule = chat.split(".job-preview {")[1].split("}")[0]
        assert "min-width: 0" in rule

    def test_the_pointer_is_captured_for_the_drag(self, chat):
        """The pointer outruns the splitter the moment the width hits either end."""
        assert "setPointerCapture" in chat
        assert "releasePointerCapture" in chat

    def test_a_drag_does_not_select_the_page(self, chat):
        assert re.search(r"\.job-modal-panel\.is-resizing\s*\{[^}]*user-select:\s*none", chat)

    def test_the_grip_is_wider_than_the_rule(self, chat):
        """2px of rule is not a target anybody hits first time."""
        rule = chat.split(".job-splitter {")[1].split("}")[0]
        width = float(re.search(r"width:\s*([0-9.]+)px", rule).group(1))
        grip = float(re.search(r"border-left:\s*([0-9.]+)px", rule).group(1))
        assert grip * 2 + width >= 16

    def test_the_preview_keeps_a_minimum_share(self, chat):
        """Otherwise a small window leaves it a strip too narrow to read."""
        fn = chat.split("function jobOptionsMax()")[1].split("\n}")[0]
        assert "JOB_PREVIEW_MIN" in fn
        assert "panel - JOB_PREVIEW_MIN" in fn

    def test_what_is_remembered_is_what_was_asked_for(self, chat):
        """Opening it once on a laptop must not shrink the big monitor's width."""
        fn = chat.split("function setJobOptionsWidth")[1].split("\n}")[0]
        saved = re.search(r'localStorage\.setItem\("job-options-width",\s*([^)]+)\)', fn)
        assert "px" in saved.group(1), "the clamped width would be the wrong thing to keep"

    def test_the_width_is_restored_when_the_modal_opens(self, chat):
        opener = chat.split("async function openJobModal")[1].split("\n}")[0]
        assert "restoreJobOptionsWidth()" in opener


class TestTheProfessorPickerMatchesTheModelPicker:
    """It was a bare <select>: the browser draws those itself.

    A different height from every other field on the page, a different chevron,
    and a list rendered by the OS that ignores the theme entirely — which is the
    same reason the model picker stopped being one.
    """

    @pytest.fixture
    def chat(self):
        return _rendered_chat()

    def test_the_select_is_gone(self, chat):
        assert "professor-select" not in chat
        assert 'id="professor-combobox"' in chat

    def test_both_pickers_are_built_from_the_same_parts(self, chat):
        for part in ("combobox", "combobox-field", "combobox-toggle", "combobox-list"):
            for picker in ("professor", "model"):
                block = chat.split(f'id="{picker}-combobox"')[1].split("</div>\n")[0] \
                    if picker == "professor" else chat.split('id="model-combobox"')[1]
                assert part in block[:1200], f"{picker} is missing {part}"

    def test_the_field_class_is_not_named_after_one_picker(self, chat):
        """It was .model-field while two fields use it."""
        assert "class=\"model-field\"" not in chat
        assert ".combobox input.combobox-field" in chat

    def test_both_chevrons_are_the_same_drawing(self, chat):
        paths = re.findall(r'class="combobox-toggle"[^>]*>\s*<svg[^>]*>\s*<path d="([^"]{40,})"', chat)
        assert len(paths) == 2 and paths[0] == paths[1]

    def test_opening_and_closing_is_written_once(self, chat):
        """Two copies is how two pickers drift apart."""
        assert chat.count("function openCombobox") == 1
        assert chat.count("function wireCombobox") == 1
        # The model picker's own versions now defer rather than duplicate.
        # openModelList() was a wrapper with no callers once the picker moved
        # into the partial; closeModelList() still has one.
        assert "function openModelList" not in chat
        assert 'function closeModelList() { closeCombobox("model"); }' in chat

    def test_the_listener_is_wired_outside_the_loader(self, chat):
        """loadProfessors() runs twice; wiring inside it stacked the handlers."""
        loader = chat.split("async function loadProfessors")[1].split("\n}")[0]
        assert "addEventListener" not in loader or "opt.addEventListener" in loader
        assert 'wireCombobox("professor");' in chat

    def test_clicking_away_closes_every_picker_on_the_page(self, chat):
        """Written once over all of them, so a second picker gets it for free.

        It used to be a line per picker in one page-wide handler, which is a
        line somebody has to remember to add.
        """
        handler = chat.split('document.addEventListener("click"')[1].split("\n});")[0]
        assert "WIRED_COMBOBOXES.forEach" in handler
        assert "professor-combobox" not in handler, "no picker should be named here"

    def test_choosing_the_same_professor_does_no_work(self, chat):
        """It reloaded every conversation and the usage panel for nothing."""
        fn = chat.split("async function chooseProfessor")[1].split("\n}")[0]
        assert "if (netid === state.professor) return;" in fn

    def test_it_is_announced_as_a_combobox(self, chat):
        # To the end of the combobox, not a fixed slice — the chevron's path
        # data alone is longer than a window sized by eye.
        block = chat.split('id="professor-combobox"')[1].split("</div>\n    </div>")[0]
        assert 'role="combobox"' in block
        assert 'aria-expanded' in block
        assert 'role="listbox"' in block


class TestPastingTextIntoAJobForm:
    """The browser side of -c/--custom: a third mode on the file field."""

    @pytest.fixture
    def chat(self):
        return (WEBUI_SRC / "templates"
                / "chat.html").read_text()

    def test_pasting_is_offered_as_a_mode(self, chat):
        assert 'if (field.allow_text) modes.push(["text", "Paste the text"]);' in chat

    def test_it_adds_a_mode_rather_than_replacing_one(self, chat):
        """A single file must stay first, and therefore the default."""
        assert 'const modes = [["file", "A single file"]];' in chat
        assert 'if (field.allow_folder) modes.push(' in chat

    def test_the_toggle_appears_for_text_alone(self, chat):
        """A field offering only pasting still needs somewhere to choose it."""
        assert "if (field.allow_folder || field.allow_text) {" in chat

    def test_pasted_text_travels_as_a_value_not_a_file(self, chat):
        fn = chat.split("function collectJobFieldValues")[1].split("\n}")[0]
        assert 'values[field.name + "_text"] = el.value;' in fn
        # The element either is a file input or it is not; that is the honest test.
        assert "if (el.files)" in fn

    def test_a_pasted_passage_satisfies_a_required_file_field(self, chat):
        """Otherwise the form refuses to start a job it has everything for."""
        assert 'files.length === 0 && !pasted' in chat

    def test_the_refusal_names_both_ways_out(self, chat):
        assert "or paste the text to translate" in chat

    def test_switching_mode_refreshes_the_preview(self, chat):
        """What was chosen in the old mode is gone, so the preview is stale."""
        fn = chat.split("const apply = (mode) => {")[1].split("\n  };")[0]
        assert "scheduleJobPreview()" in fn

    def test_typing_refreshes_the_preview(self, chat):
        fn = chat.split("const apply = (mode) => {")[1].split("\n  };")[0]
        assert 'replacement.addEventListener("input", scheduleJobPreview)' in fn

    def test_the_box_is_big_enough_for_a_passage(self, chat):
        fn = chat.split("const apply = (mode) => {")[1].split("\n  };")[0]
        rows = int(re.search(r"replacement\.rows = (\d+)", fn).group(1))
        assert rows >= 5, "one line misrepresents what goes in it"


class TestTheProfessorPickerIsFieldHeight:
    """It stretched to about a hundred pixels tall, chevron floating mid-way.

    `.combobox` carried `flex: 1`, written for the top bar — a flex row, where
    that means "take the remaining width". The sidebar is a flex column, so the
    same declaration told the professor picker to take the remaining *height*.
    The chevron is positioned at top: 50%, so it centred itself in the result.
    """

    @pytest.fixture
    def chat(self):
        return _rendered_chat()

    def test_the_component_claims_no_space_of_its_own(self, chat):
        rule = chat.split(".combobox { ")[1].split("}")[0]
        assert "flex:" not in rule, (
            "a component that grows on its own stretches wherever it is put — "
            "here, down a column"
        )

    def test_the_top_bar_still_gives_it_the_room(self, chat):
        """Dropping the grow must not have collapsed the model field."""
        assert ".model-picker .combobox { flex: 1; }" in chat

    def test_the_sidebar_is_still_a_column(self, chat):
        """If this ever stops being true, the rule above is why it mattered."""
        rule = chat.split("#sidebar { ")[1].split("}")[0]
        assert "flex-direction: column" in rule

    def test_the_chevron_still_centres_on_the_field(self, chat):
        """Right for a field-height box; that was never the bug."""
        rule = chat.split(".combobox-toggle {")[1].split("}")[0]
        assert "top: 50%" in rule and "translateY(-50%)" in rule


class TestTheInstallEntryInThePluginMenu:

    def test_it_is_offered(self):
        chat = _rendered_chat()
        assert "Install plugin…" in chat

    def test_it_comes_last(self):
        """Everything above it runs something already installed."""
        chat = _rendered_chat()
        picker = chat[chat.index("function renderActionPicker"):]
        picker = picker[:picker.index("function openInstallDialog")]
        assert picker.index("state.pluginActions.forEach") < picker.index(
            "addInstallEntry(picker)")

    def test_it_is_offered_even_when_no_plugin_has_an_action(self):
        """A sandbox with nothing installed is exactly where this is wanted.

        There used to be an early return on the empty case, which meant the
        one menu that most needed this entry was the one menu without it.
        Measured inside the function: "action-picker-empty" appears in the
        stylesheet too, and slicing from there ran the check across half the
        file.
        """
        chat = _rendered_chat()
        body = chat[chat.index("function renderActionPicker"):]
        body = body[:body.index("addInstallEntry(picker);")]
        assert "return;" not in body, "it gives up before offering the entry"

    def test_the_dialog_says_what_a_plugin_is(self):
        chat = _rendered_chat()
        assert "runs alongside everything" in chat
        assert "API keys" in chat

    def test_the_folder_box_stops_guessing_once_it_is_typed_in(self):
        chat = _rendered_chat()
        assert 'dataset.editedByHand === "true"' in chat

    def test_it_waits_for_the_sandbox_rather_than_guessing(self):
        chat = _rendered_chat()
        assert "waitForTheSandboxToComeBack" in chat
        assert "setTimeout(ask, 500)" in chat

    def test_what_git_says_is_shown_as_text(self):
        """git's own words come back through here, and they are words."""
        chat = _rendered_chat()
        at = chat.index('error.textContent = readableError(e)')
        assert "innerHTML" not in chat[at - 300:at + 100]

    def test_and_the_thing_that_shows_them_exists(self):
        """This test used to check only that the line calling readableError
        was written, which it was — on a page that did not have the function.
        Every failed install therefore threw a ReferenceError inside its own
        catch, and the dialog sat on "Downloading…" saying nothing.
        """
        import re

        chat = _rendered_chat()
        assert re.search(r"function\s+readableError\s*\(", chat), (
            "chat.html calls readableError and nothing defines it")


class TestAModalThatIsShownIsAlsoVisible:
    """`.modal-backdrop` is `opacity: 0` and covers the whole window at
    z-index 100. Unhiding one without adding `.open` therefore does not open
    anything — it lays an invisible sheet over the page that swallows every
    click, which is what "nothing happens and the window is unresponsive"
    turned out to be."""

    def _chat(self):
        return _rendered_chat()

    def test_the_backdrop_is_transparent_until_it_is_opened(self):
        """The premise. If this stops being true the rest stops mattering."""
        chat = self._chat()
        rule = chat[chat.index(".modal-backdrop {"):]
        assert "opacity: 0" in rule[:rule.index("}")]
        assert ".modal-backdrop.open { opacity: 1; }" in chat

    def test_every_backdrop_that_is_unhidden_is_also_opened(self):
        """Whichever modal it is. Each one is a separate chance to make the
        page unclickable in a way that looks like nothing happening."""
        import re

        chat = self._chat()
        script = "\n".join(re.findall(r"<script>(.*?)</script>", chat, re.S))
        missing = []
        for shown in re.finditer(r"(\w+)\.hidden = false;", script):
            name = shown.group(1)
            if "backdrop" not in name.lower():
                continue
            # The class has to be added near where it is unhidden.
            after = script[shown.end():shown.end() + 400]
            if 'classList.add("open")' not in after:
                line = script[:shown.start()].count("\n") + 1
                missing.append(f"line {line}: {name}")
        assert not missing, (
            "unhidden without being made visible, so it covers the page "
            "invisibly:\n" + "\n".join(missing))

    def test_the_install_dialog_can_be_left(self):
        """A modal nobody can dismiss is the same problem arrived at slowly."""
        chat = self._chat()
        assert "install-plugin-close" in chat
        # Clicking away, and Escape, as the other modals allow. How clicking
        # away is decided is TestAModalDoesNotCloseOnADragThatLeavesIt's
        # business; all that matters here is that it is offered at all.
        assert ('closeWhenTheBackdropItselfIsClicked("install-plugin-backdrop", '
                "closeInstallDialog)") in chat
        # This dialog's own Escape handler, not the first one in the file —
        # the combobox has one too, several hundred lines earlier.
        at = chat.index('!document.getElementById("install-plugin-backdrop").hidden')
        assert 'e.key === "Escape"' in chat[at - 120:at]
        assert "closeInstallDialog" in chat[at:at + 120]

    def test_it_is_hidden_only_after_it_has_faded(self):
        chat = self._chat()
        close = chat[chat.index("function closeInstallDialog"):]
        close = close[:close.index("\n}")]
        assert 'classList.remove("open")' in close
        assert "setTimeout" in close


class TestAModalDoesNotCloseOnADragThatLeavesIt:
    """Select text in a field, drag past the edge of the panel, let go — and
    the modal shut, losing whatever had been filled in.

    A click event is delivered to the nearest ancestor the two ends of the
    gesture have in common, so a drag beginning in a field and ending outside
    reports the backdrop as its target. Asking "was this click on the
    backdrop?" is therefore true for a text selection that happened to
    overshoot."""

    def _script(self):
        import re

        return "\n".join(re.findall(r"<script>(.*?)</script>", _rendered_chat(), re.S))

    def test_both_ends_of_the_gesture_are_checked(self):
        script = self._script()
        helper = script[script.index("function closeWhenTheBackdropItselfIsClicked"):]
        helper = helper[:helper.index("\n}")]
        assert 'addEventListener("mousedown"' in helper
        assert 'addEventListener("mouseup"' in helper
        assert "startedOnTheBackdrop && endedOnTheBackdrop" in helper

    def test_it_does_not_decide_from_the_click_target(self):
        """Which is the ancestor in common, and so the backdrop for any drag
        between it and the panel — in either direction."""
        script = self._script()
        helper = script[script.index("function closeWhenTheBackdropItselfIsClicked"):]
        helper = helper[:helper.index("\n}")]
        assert 'addEventListener("click"' not in helper

    def test_the_flag_does_not_survive_into_the_next_gesture(self):
        """Otherwise a drag out of a field arms the next click anywhere."""
        script = self._script()
        helper = script[script.index("function closeWhenTheBackdropItselfIsClicked"):]
        helper = helper[:helper.index("\n}")]
        assert helper.index("startedOnTheBackdrop = false;") < helper.index("if (both)")

    def test_every_modal_uses_it(self):
        """Four modals, one rule. A fifth written by hand would be a fifth
        chance to lose what somebody had typed."""
        import re

        script = self._script()
        by_hand = re.findall(r'e\.target\.id === "([a-z-]*backdrop)"', script)
        assert not by_hand, f"deciding for itself rather than using the helper: {by_hand}"
        used = set(re.findall(
            r'closeWhenTheBackdropItselfIsClicked\("([a-z-]+)"', script))
        assert used == {"install-plugin-backdrop", "job-modal-backdrop",
                        "settings-modal-backdrop", "filter-modal-backdrop"}, used


class TestAJobGetsAConversationToItself:
    """What a job produces is a piece of work with its own documents, its own
    settings note and its own outputs. Dropping that into the middle of a
    conversation about something else leaves both harder to read and harder
    to cite, so a job starts its own — unless the one in view has nothing in
    it yet, which is already the fresh one it would otherwise go and make."""

    def _decision(self):
        """The lines that choose where a job runs."""
        import re

        script = "\n".join(re.findall(r"<script>(.*?)</script>", _rendered_chat(), re.S))
        start = script.index("// A job gets a conversation to itself.")
        return script[start:script.index('const startBtn', start)]

    def test_an_empty_conversation_is_used_as_it_is(self):
        block = self._decision()
        assert "!state.conversationId || state.conversationHasMessages" in block

    def test_a_conversation_with_anything_in_it_is_left_alone(self):
        """The condition above is the whole of it, but it is easy to read the
        wrong way round, so this says which way it goes."""
        block = self._decision()
        at = block.index("state.conversationHasMessages")
        # Inside the branch that makes a new one, not outside it.
        assert "beginConversationOn" in block[at:]

    def test_both_ways_of_starting_one_do_the_same_thing(self):
        """The button and a job both mean "a new conversation", and that has
        to include the sampling settings — those belong to a conversation and
        must not be inherited by the one replacing it. The job path used to
        set the id and the model and nothing else."""
        import re

        script = "\n".join(re.findall(r"<script>(.*?)</script>", _rendered_chat(), re.S))
        assert script.count("async function beginConversationOn") == 1
        helper = script[script.index("async function beginConversationOn"):]
        helper = helper[:helper.index("\n}")]
        for reset in ("conversationHasMessages = false", "state.sampling = {",
                      "renderMessages(conv)"):
            assert reset in helper, reset
        # The button and the job both go through it.
        assert script.count("beginConversationOn(") == 3, (
            "one definition and two callers — the New conversation button and "
            "a job")

    def test_sending_a_message_still_makes_its_own(self):
        """Deliberately not through the helper. Sending from the blank view
        carries whatever sampling was set in the composer straight into the
        request, so resetting it on the way would throw away the temperature
        somebody had just chosen. "A new conversation" and "the conversation
        this message is starting" are not the same act.
        """
        import re

        script = "\n".join(re.findall(r"<script>(.*?)</script>", _rendered_chat(), re.S))
        send = script[script.index("appendPendingMessage(text);") - 900:]
        send = send[:send.index("appendPendingMessage(text);")]
        assert 'api("/api/conversations"' in send
        assert "state.sampling = {" not in send

    def test_the_new_conversation_starts_on_the_model_the_job_uses(self):
        """There being nothing else to go on."""
        block = self._decision()
        assert "beginConversationOn(model)" in block


class TestTheTranscriptDoesNotDragTheReaderAround:
    """Text arriving used to pull the view to the bottom whatever the reader
    was doing — every streamed chunk, and every poll of a running job, which
    happens on a timer. Scrolling up to read an earlier page lasted until the
    next tick."""

    def _script(self):
        import re

        return "\n".join(re.findall(r"<script>(.*?)</script>", _rendered_chat(), re.S))

    def test_nothing_scrolls_the_transcript_except_the_two_named_ways(self):
        """One place decides what "the end" means and one place goes there."""
        script = self._script()
        raw = script.count("scrollTop = box.scrollHeight")
        assert raw == 1, f"{raw} places move the transcript by hand"
        assert "function goToTheEnd" in script

    def test_a_streamed_chunk_asks_before_it_lands(self):
        """Adding the text is what moves the end away, so asking afterwards
        always answers yes."""
        script = self._script()
        block = script[script.index("const following = isWatchingTheEnd(box);"):]
        block = block[:block.index("if (following) goToTheEnd(box);")]
        assert "assistantDiv.textContent += event.text;" in block

    def test_it_asks_every_chunk_rather_than_deciding_once(self):
        """So that scrolling back down yourself starts it following again,
        with nothing to press."""
        script = self._script()
        stream = script[script.index("assistantDiv.textContent += event.text;") - 400:]
        stream = stream[:stream.index("if (following) goToTheEnd(box);")]
        assert "const following = isWatchingTheEnd(box);" in stream

    def test_a_re_render_puts_the_reader_back_rather_than_leaving_them(self):
        """Emptying the box collapses the scroll position to the top, so
        merely declining to jump to the bottom moves them to the beginning —
        which is worse than the jump it replaced."""
        script = self._script()
        helper = script[script.index("function rememberWhereTheReaderIs"):]
        helper = helper[:helper.index("\n}\n")]
        assert "const wasAt = box.scrollTop;" in helper
        assert "else box.scrollTop = wasAt;" in helper

    def test_opening_a_conversation_still_lands_at_the_end(self):
        """Where somebody had scrolled to in the conversation being left says
        nothing about where they want to be in this one."""
        script = self._script()
        opening = script[script.index("async function openConversation"):]
        opening = opening[:opening.index("if (conv.active_job_id)")]
        assert "goToTheEnd(" in opening

    def test_sending_a_message_still_brings_you_down(self):
        """You pressed send; being shown what you sent is the answer to it."""
        script = self._script()
        pending = script[script.index("function appendPendingMessage"):]
        pending = pending[:pending.index("\n}\n")]
        assert "goToTheEnd(box)" in pending
        assert "isWatchingTheEnd" not in pending

    def test_close_to_the_end_counts_as_the_end(self):
        """Scroll positions come back fractional and a trackpad leaves you a
        pixel short; exact equality would stop it following for no reason
        anybody could see."""
        script = self._script()
        assert "const AT_THE_BOTTOM = 24;" in script
        check = script[script.index("function isWatchingTheEnd"):]
        check = check[:check.index("\n}")]
        assert "<= AT_THE_BOTTOM" in check


class TestAConversationOpensFromAnywhereOnItsRow:
    """The row is the target, because the row is what the list draws.

    The hand cursor was on the row and the listener was on the title inside it,
    so every part of the row the words did not reach — the padding, and the
    height the menu button adds beyond one line of text — invited a click and
    then ignored it.
    """

    def _chat(self) -> str:
        return _rendered_chat()

    def test_the_row_carries_the_listener_not_the_title(self):
        chat = self._chat()
        assert 'item.addEventListener("click"' in chat
        assert 'titleSpan.addEventListener("click"' not in chat, \
            "the title is listening again, and the row's edges are dead"

    def test_the_hand_and_the_listener_are_on_the_same_element(self):
        """The defect in one line: whatever shows a pointer must accept one."""
        chat = self._chat()
        rule = chat.split(".conv-item {")[1].split("}")[0]
        assert "cursor: pointer" in rule
        assert 'item.addEventListener("click"' in chat

    def test_renaming_still_does_not_reopen_the_conversation(self):
        """Clicking into the editable title places a cursor, nothing more."""
        chat = self._chat()
        assert "titleSpan.isContentEditable" in chat

    def test_the_menu_keeps_its_own_meaning(self):
        """The row is the menu button's ancestor now, so its clicks must not
        also open the conversation."""
        chat = self._chat()
        assert '.closest(".conv-menu-btn, .conv-menu")' in chat

    def test_a_conversation_can_be_opened_without_a_mouse(self):
        """There was no way to at all: a bare div, no tabindex, no key handler."""
        chat = self._chat()
        assert "item.tabIndex = 0" in chat
        assert 'item.addEventListener("keydown"' in chat
        assert 'e.key !== "Enter" && e.key !== " "' in chat

    def test_the_open_conversation_says_so_to_a_screen_reader(self):
        """The active row was marked by background colour alone."""
        assert 'item.setAttribute("aria-current", "true")' in self._chat()

    def test_a_row_does_not_trap_the_menu_hanging_from_it(self):
        """The row is positioned so the menu can hang from it, and must stop
        there. Given a z-index as well it became a stacking context, which
        held the menu's own z-index inside it — so the next row along, at the
        same level and later in the page, painted over an open menu and the
        conversation titles showed through it.

        The heading keeps its z-index: it is opaque, and a row scrolled
        underneath it cannot be seen, so it should not be clickable either.
        """
        chat = self._chat()
        row = chat.split(".conv-item {")[1].split("}")[0]
        assert "position: relative" in row
        assert "z-index" not in row, "an open menu will be painted over by the next row"
        heading = chat.split(".conv-group {")[1].split("}")[0]
        assert "z-index: 1" in heading


class TestTheFormatsAreListedInOnePlace:
    """They are offered in two menus, and two copies would not stay the same."""

    def test_the_menu_is_built_from_the_shared_list(self):
        chat = _rendered_chat()
        assert "const EXPORT_FORMATS = [" in chat
        # The literal the menu used to carry inline is gone.
        assert '[["docx", "Export as Word (.docx)"], ["pdf"' not in chat

    def test_plain_text_is_offered(self):
        chat = _rendered_chat()
        assert '["txt", "Export as plain text (.txt)"]' in chat

    def test_the_page_offers_exactly_what_the_server_can_write(self):
        """A format in one and not the other is a menu item that 400s, or a
        format nobody can reach."""
        export_module = sys.modules["_pu_webui_export"]
        chat = _rendered_chat()
        listed = chat.split("const EXPORT_FORMATS = [")[1].split("];")[0]
        offered = set(re.findall(r'\["(\w+)",', listed))
        assert offered == set(export_module.FORMATS)


class TestTheBarAboveTheMessageBox:
    """What is open, what it cost, and the two things done with a finished one.

    The conversation's name appeared only in the sidebar list, truncated; what
    a single conversation had cost was not shown anywhere at all, and the only
    way to its folder or its transcript was the hover menu on its sidebar row.
    """

    def _chat(self) -> str:
        return _rendered_chat()

    def test_it_sits_between_the_transcript_and_the_message_box(self):
        """A bar about this conversation belongs against the conversation, not
        in the top bar, which is about the sandbox."""
        page = self._chat()
        assert page.index('id="messages"') < page.index('id="conv-bar"') < page.index('id="composer"')

    def test_it_is_hidden_until_a_conversation_is_open(self):
        page = self._chat()
        bar = page[page.index('<div id="conv-bar"'):]
        assert bar[:bar.index(">")].find("hidden") != -1

    def test_it_shows_the_whole_title_and_keeps_it_reachable_when_cut(self):
        page = self._chat()
        assert 'id="conv-bar-title"' in page
        rule = page.split("#conv-bar-title {")[1].split("}")[0]
        assert "text-overflow: ellipsis" in rule
        # Cut on screen, still readable on hover — the sidebar's own convention.
        assert 'titleEl.title = conv.title' in page

    def test_the_name_is_renamed_by_clicking_it(self):
        """The name of the open conversation is the obvious thing to reach for;
        before this the only way to rename one was the hover menu on its
        sidebar row, which is a different copy of the same name."""
        page = self._chat()
        assert 'document.getElementById("conv-bar-title").addEventListener("click", renameFromConversationBar)' in page
        # The sidebar's editing, not a second implementation of it.
        body = page.split("function renameFromConversationBar() {")[1].split("\n}")[0]
        assert "startRenaming(titleEl, state.conversationId" in body

    def test_the_pointer_says_the_name_can_be_clicked(self):
        """Nothing else in the bar is clickable text, so without the hand
        cursor there is nothing to suggest this is."""
        rule = self._chat().split("#conv-bar-title {")[1].split("}")[0]
        assert "cursor: pointer" in rule
        assert "#conv-bar-title:hover {" in self._chat()

    def test_being_typed_into_it_is_a_box_and_not_a_label(self):
        page = self._chat()
        rule = page.split('#conv-bar-title[contenteditable="true"] {')[1].split("}")[0]
        # A name being edited has to be readable whole, and the pointer that
        # invited the click would now be wrong.
        assert "white-space: normal" in rule
        assert "cursor: text" in rule

    def test_the_keyboard_reaches_it_too(self):
        page = self._chat()
        assert '<div id="conv-bar-title" role="button" tabindex="0">' in page
        handler = page.split('document.getElementById("conv-bar-title").addEventListener("keydown"')[1][:500]
        assert 'e.key !== "Enter" && e.key !== " "' in handler
        # Once it is a box those keys belong to the typing.
        assert "isContentEditable" in handler

    def test_a_half_written_rename_survives_a_redraw(self):
        """A reply arriving, or the first reply's automatic title, calls
        renderMessages — which would otherwise overwrite what is being typed."""
        body = self._chat().split("function updateConversationBar(conv) {")[1][:900]
        assert "if (!titleEl.isContentEditable) {" in body

    def test_an_abandoned_rename_puts_the_name_back(self):
        """Escape, or emptying the box, commits nothing — and the bar is not
        redrawn afterwards the way the sidebar list is, so it would be left
        naming the conversation something it is not called."""
        finish = self._chat().split("const finish = async (commit) => {")[1].split("\n  };")[0]
        assert "titleSpan.textContent = currentTitle;" in finish.split("} else {")[1]

    def test_the_spend_is_summed_from_the_messages_in_hand(self):
        """There is no per-conversation total on the server to ask for; usage
        is kept per person and per month."""
        page = self._chat()
        assert "function conversationTotals(" in page
        assert "m.prompt_tokens" in page and "m.completion_tokens" in page
        assert "fmtSmallMoney(totals.cost)" in page

    def test_a_conversation_costing_less_than_a_penny_is_not_shown_as_nothing(self):
        """The month's figure is dollars; one conversation is often a penny or
        two, and $0.00 beside a conversation that plainly cost something reads
        as a broken number."""
        page = self._chat()
        fn = page.split("function fmtSmallMoney(")[1].split("\n}")[0]
        assert "toFixed(4)" in fn
        assert "0.01" in fn

    def test_the_figures_do_not_jitter_as_they_change(self):
        rule = self._chat().split("#conv-bar-spend {")[1].split("}")[0]
        assert "tabular-nums" in rule

    def test_the_folder_button_is_hidden_where_it_could_not_work(self):
        """Hidden, not disabled — the convention the Browse buttons set."""
        page = self._chat()
        assert 'document.getElementById("conv-bar-folder").hidden = !state.canReveal' in page

    def test_the_download_picker_offers_the_shared_list(self):
        page = self._chat()
        picker = page.split("function toggleFormatPicker(")[1].split("\n}")[0]
        assert "EXPORT_FORMATS.forEach" in picker
        assert "exportConversation(state.conversationId, format)" in picker

    def test_the_picker_opens_upward(self):
        """The bar is at the bottom of the page, so a menu below it is off
        the end."""
        rule = self._chat().split(".format-picker {")[1].split("}")[0]
        assert "bottom: calc(100% + 6px)" in rule

    def test_the_picker_closes_like_every_other_floating_panel(self):
        page = self._chat()
        closer = page.split('if (!document.getElementById("model-add").contains')[1][:600]
        assert "closeFormatPicker()" in closer

    def test_the_bar_follows_whatever_the_transcript_shows(self):
        """renderMessages already receives the whole conversation and runs when
        one is opened, when a reply finishes, and while a job is running."""
        page = self._chat()
        body = page.split("function renderMessages(conv) {")[1][:600]
        assert "updateConversationBar(conv)" in body

    def test_renaming_the_open_conversation_renames_it_here_too(self):
        """renderMessages is not called on that path."""
        assert "id === state.conversationId" in self._chat()

    def test_the_bar_changes_with_the_theme_like_every_other_surface(self):
        """It is a panel-coloured surface, so it eases like the rest of them."""
        page = self._chat()
        rule = page.split("body, #sidebar")[1].split("{")[0]
        assert "#conv-bar" in rule


class TestAReplyNobodyCouldPrice:
    """A reply whose closing part never arrived costs nothing, and says nothing.

    The stream simply stops; no error is raised, because as far as this end is
    concerned nothing went wrong. The usage never comes, and the turn is saved
    looking free — while the provider bills for every token it read and wrote.
    Whether the words are all there is not knowable from here.
    """

    def _chat(self) -> str:
        return _rendered_chat()

    def test_a_stream_that_never_says_it_finished_is_marked_as_such(self):
        chat_service = sys.modules["src.services.chat_service"]
        import inspect

        source = inspect.getsource(chat_service.ChatService.stream_message)
        assert "finish_reason" in source
        assert "unfinished = finish_reason is None" in source

    def test_a_reply_is_not_called_cut_off_on_that_evidence_alone(self):
        """A stream that stops without its closing part is the only thing the
        sandbox sees, and a whole reply looks the same as a truncated one. Said
        as a finding, it told a professor their reply had been cut off for a
        month when it plainly ran to its end."""
        chat = self._chat()
        shown = chat.split('meta.className = "meta meta-unpriced"')[1]
        unpriced = shown.split("meta.textContent =")[1].split(";")[0]
        assert "cut off" not in unpriced, unpriced
        assert "may be missing its end" in unpriced
        # The money is still stated plainly: no cost recorded here.
        assert "no cost" in unpriced

    def test_a_reply_from_somebody_elses_service_is_not_called_uncounted(self):
        """An alternate endpoint has no prices in the sandbox on purpose, so a
        reply from one is recorded with tokens and no money — not as spending
        the university's bill is missing. BaseService has always known this;
        the streaming path did not, and sent professors to OIT to ask about a
        call OIT never saw."""
        chat_service = sys.modules["src.services.chat_service"]
        import inspect

        source = inspect.getsource(chat_service.ChatService.stream_message)
        assert "elif not self.endpoint_name:" in source

    def test_a_streamed_reply_is_priced_by_the_service_that_answered(self):
        """Without the endpoint, a model on somebody's own cluster was priced
        at whatever the catalog held for a name like it."""
        chat_service = sys.modules["src.services.chat_service"]
        import inspect

        source = inspect.getsource(chat_service.ChatService.stream_message)
        recording = source.split("record_usage(")[1].split(")")[0]
        assert "endpoint=self.endpoint_name" in recording

    def test_an_unpriced_turn_is_noted_against_the_month(self):
        """Not silently passed over: the month has to be able to say how much
        of itself it could not count."""
        chat_service = sys.modules["src.services.chat_service"]
        import inspect

        source = inspect.getsource(chat_service.ChatService.stream_message)
        assert "record_unreported_call" in source

    def test_the_transcript_says_why_a_reply_has_no_cost(self):
        """Blank reads as free."""
        chat = self._chat()
        assert "never said what this reply used" in chat
        assert "did not report this reply's cost" in chat

    def test_the_spend_panel_offers_the_correction(self):
        chat = self._chat()
        assert 'id="spend-uncounted"' in chat
        assert "Ask OIT" in chat
        assert 'id="spend-adjust-btn"' in chat

    def test_the_month_is_footnoted_rather_than_warned_about(self):
        """The warning sat open above the figures on every conversation for the
        rest of the month. Once read there is nothing to do about it until the
        bill arrives, so it is a footnote on the figure it is about: a mark that
        says it when asked."""
        chat = self._chat()
        # The mark hangs off the month's figure, and the words wait behind it.
        figure = chat.split('<h2>This month</h2>')[1].split("</div>")[0]
        assert 'id="spend-uncounted-btn"' in figure
        assert 'aria-controls="spend-uncounted"' in figure
        warning = chat.split('<p class="spend-warning" id="spend-uncounted"')[1].split(">")[0]
        assert "hidden" in warning
        # And it opens and shuts, rather than only opening.
        toggle = chat.split('document.getElementById("spend-uncounted-btn").addEventListener')[1][:400]
        assert "warning.hidden = !opening" in toggle
        assert "aria-expanded" in toggle

    def test_the_words_sit_evenly_inside_the_box_and_clear_of_the_figure(self):
        """A wrapper paragraph inside the box added its own margins to the
        box's padding, so the words sat further from the top and bottom edges
        than from the sides. The space that belongs outside the box is given
        outside it, at the step the other things under a figure use."""
        chat = self._chat()
        # One element, written into directly — the same shape as the warning
        # above the figures.
        assert "spend-uncounted-text" not in chat
        rule = chat.split(".spend-warning {")[1].split("}")[0]
        assert "padding: var(--space-2);" in rule
        assert "#spend-uncounted { margin: var(--space-2) 0; }" in chat

    def test_the_mark_is_there_only_where_there_is_something_to_say(self):
        chat = self._chat()
        assert "uncountedMark.hidden = state.usageMonth.unreported === 0" in chat

    def test_an_opened_footnote_stays_open_while_being_read(self):
        """The panel is redrawn after every reply, and a warning that shut
        itself mid-sentence would be its own annoyance."""
        chat = self._chat()
        assert 'uncounted.hidden = uncountedMark.getAttribute("aria-expanded") !== "true"' in chat

    def test_the_correction_is_named_for_what_it_does(self):
        """"Compare with the bill" described the errand rather than the
        control, and read as homework."""
        chat = self._chat()
        control = chat.split('id="spend-adjust-btn"')[1].split("</button>")[0]
        assert "Adjust total" in control

    def test_the_correction_can_be_reached_without_anything_going_wrong(self):
        """It lived inside the warning about unpriced replies, so it existed
        only while that warning did — which is almost never, and never at all
        for a month whose unpriced replies happened before this noticed them.
        A month can disagree with the bill for reasons the sandbox never sees.
        """
        chat = self._chat()
        warning = chat.split('id="spend-uncounted"')[1].split("</p>")[0]
        assert "spend-adjust-btn" not in warning, "the control is inside the warning again"
        # Sitting with the figure it corrects, and not hidden.
        after_month = chat.split('id="spend-month"')[1].split("<h2>")[0]
        assert "spend-adjust-btn" in after_month
        button = after_month.split('id="spend-adjust-btn"')[0].rsplit("<button", 1)[1]
        assert "hidden" not in button

    def test_the_box_does_not_claim_a_problem_that_did_not_happen(self):
        """It can be opened at any time now, so the line about unpriced
        replies has to be the exception rather than the greeting."""
        chat = self._chat()
        assert 'id="adjust-why"' in chat
        assert 'document.getElementById("adjust-why").hidden = !(month.unreported > 0)' in chat

    def test_the_box_asks_for_the_total_not_the_difference(self):
        """The total is the number on the bill; the difference is arithmetic."""
        chat = self._chat()
        assert "What the bill says" in chat
        assert "function previewAdjustment(" in chat

    def test_nothing_assumes_the_figure_is_wrong_before_it_is_compared(self):
        """The sandbox has no way of knowing whether a month disagrees with
        the bill until someone says what the bill reads. Language that calls
        it a correction in advance answers that question for them."""
        chat = self._chat()
        control = chat.split('id="spend-adjust-btn"')[1].split("</button>")[0]
        assert "Correct" not in control, control
        # The one place it may say so is where a reply genuinely went
        # unpriced, and that is shown only on that condition.
        opening = chat.split('id="adjust-why"')[1].split("</p>")[0]
        assert "could not be priced" in opening
        assert 'document.getElementById("adjust-why").hidden = !(month.unreported > 0)' in chat
        # And agreement is a stated outcome, not a silent one.
        assert "nothing to record" in chat

    def test_a_corrected_month_still_shows_what_was_measured(self):
        chat = self._chat()
        assert 'id="spend-adjusted"' in chat
        assert "measured," in chat


class TestTheConversationListCanBeFiltered:
    """A long list narrowed to what someone is looking for, by a button that
    says when it is doing so and a cross that stops it."""

    def _chat(self) -> str:
        return _rendered_chat()

    def _script(self) -> str:
        import re

        return "\n".join(re.findall(r"<script>(.*?)</script>", self._chat(), re.S))

    def _function(self, name: str) -> str:
        return self._script().split(f"function {name}(")[1].split("\n}\n")[0]

    def test_the_button_sits_beside_the_plus_and_says_whether_it_is_on(self):
        page = self._chat()
        header = page[page.index("<h2>Conversations</h2>"):page.index('id="conv-list"')]
        assert 'id="conv-filter"' in header and 'id="new-conv"' in header
        button = header[header.index('id="conv-filter"'):]
        assert 'aria-pressed="false"' in button[:button.index(">")]

    def test_it_turns_orange_while_on(self):
        rule = self._chat().split('#conv-filter[aria-pressed="true"] {')[1].split("}")[0]
        assert "color: var(--orange)" in rule

    def test_the_cross_appears_only_while_on_and_puts_everything_back(self):
        page = self._chat()
        cross = page[page.index('id="conv-filter-clear"'):]
        assert "hidden" in cross[:cross.index(">")]
        # .icon-btn sets a display of its own, which would override [hidden];
        # TestHiddenMeansHidden checks that every icon button says otherwise.
        assert 'class="icon-btn neutral"' in cross[:cross.index(">")]
        update = self._function("updateFilterButton")
        assert 'getElementById("conv-filter-clear").hidden = !active' in update
        clear = self._function("clearFilter")
        assert "state.filter = defaultFilter();" in clear
        assert 'state.sort = "newest";' in clear

    def test_a_different_order_counts_as_on(self):
        """It changes what is at the top as surely as a filter does."""
        assert 'state.sort !== "newest"' in self._function("filterIsActive")

    def test_the_list_is_filtered_before_any_heading_goes_in(self):
        render = self._function("renderConversationList")
        assert render.index("state.conversations.filter(conversationMatches)") \
            < render.index("conversationAgeGroup(")

    def test_a_list_filtered_to_nothing_says_so(self):
        render = self._function("renderConversationList")
        assert "No conversation matches this filter." in render
        assert 'addEventListener("click", clearFilter)' in render

    def test_the_modal_offers_every_kind_of_filter_and_the_order(self):
        page = self._chat()
        modal = page[page.index('id="filter-modal-backdrop"'):page.index('id="adjust-modal-backdrop"')]
        for field in ("filter-text", "filter-models", "filter-ages", 'name="filter-kind"',
                      "filter-cost-min", "filter-cost-max",
                      "filter-tokens-min", "filter-tokens-max", "filter-sort"):
            assert field in modal, field
        for sort in ("newest", "oldest", "cost", "tokens", "title"):
            assert f'<option value="{sort}">' in modal

    def test_the_models_are_grouped_by_service_then_company(self):
        """The sandbox's own models first, then each service somebody added,
        and the companies inside each. A flat run of every model in the
        catalog is a lot to read when you know which you want."""
        grouping = self._function("groupModelsByService")
        assert "state.modelGroups[name]" in grouping
        assert "where.service || SANDBOX_SERVICE" in grouping
        assert 'where.company || "Other"' in grouping, "a model nobody could place needs a group too"
        assert 'inOrder(SANDBOX_SERVICE, null)' in grouping, "the sandbox is not listed first"
        assert 'inOrder(null, "Other")' in grouping, "Other is not listed last"

    def test_a_service_or_company_ticks_and_unticks_all_of_its_models(self):
        tie = self._function("tieGroupsToTheirModels")
        assert "group.models.forEach(m => { m.checked = group.box.checked; });" in tie
        # Half-ticked while only some of them are, so the box never claims
        # more than is true.
        assert "box.indeterminate = ticked > 0 && ticked < models.length;" in tie
        # Every box is brought up to date after any change: ticking a company
        # changes what its service's box should show.
        assert "container.onchange" in tie

    def test_a_service_holds_every_model_of_every_company_in_it(self):
        fill = self._function("fillFilterModal")
        assert "serviceModels.push(model.box);" in fill
        assert "groups.push({ box: serviceRow.box, models: serviceModels });" in fill

    def test_only_the_models_themselves_are_read_back(self):
        """The company boxes are a way of ticking; what the filter is made of
        is the models."""
        apply = self._function("applyFilterModal")
        assert 'ticked("#filter-models .filter-model-box:checked")' in apply

    def test_the_models_scroll_instead_of_pushing_the_rest_away(self):
        """The one part that grows as a catalog does."""
        page = self._chat()
        assert 'class="filter-scroll" id="filter-models"' in page
        rule = page.split(".filter-scroll {")[1].split("}")[0]
        assert "overflow-y: auto" in rule
        assert "max-height" in rule

    def test_a_service_and_a_company_read_as_headings(self):
        """And keep doing so: .job-field-checkbox label sets the weight back
        to normal further down the file, and would win a tie by being later."""
        page = self._chat()
        for box in ("filter-service-box", "filter-company-box"):
            rule = page.split(f".job-field-checkbox .{box} + label {{")[1]
            assert "font-weight: 600" in rule.split("}")[0], box

    def test_it_is_laid_out_the_way_a_settings_card_is(self):
        """One interface, not two. The sections are headed and spaced like a
        card's, the explanations are .hint, and the figures sit on one line
        of labelled fields the way the settings page lays fields out."""
        page = self._chat()
        modal = page[page.index('id="filter-modal-backdrop"'):page.index('id="adjust-modal-backdrop"')]
        assert modal.count('class="filter-section"') >= 5
        assert 'class="hint"' in modal
        assert '<div class="inline-fields">' in modal
        assert modal.count("<label for=\"filter-") >= 6, "a field without a label of its own"
        headings = page.split(".filter-section > legend, .filter-section > h3 {")[1].split("}")[0]
        assert "font-size: var(--text-md)" in headings, "not the size a card's heading is"

    def test_the_shared_layout_lives_in_the_shared_partial(self):
        """.inline-fields is used by the settings page and by this modal, so
        it belongs where both of them read it from."""

        templates = WEBUI_SRC / "templates"
        assert ".inline-fields {" in (templates / "_forms.html").read_text()
        assert ".inline-fields {" not in (templates / "settings.html").read_text()

    def test_a_model_ticked_elsewhere_stays_ticked_in_the_modal(self):
        """One chosen from the sidebar may belong to no conversation here."""
        assert "const names = new Set(filter.models);" in self._function("fillFilterModal")

    def test_nothing_changes_until_the_filter_is_applied(self):
        """Cancel, Escape and the cross close without touching the list."""
        script = self._script()
        assert "state.filter" not in self._function("closeFilterModal")
        assert "state.filter" not in self._function("openFilterModal").replace(
            "fillFilterModal(state.filter, state.sort)", "")
        assert 'getElementById("filter-modal-backdrop").hidden) {\n    closeFilterModal();' in script

    def test_switching_professor_starts_with_the_whole_list(self):
        choose = self._script().split("async function chooseProfessor(")[1].split("\n}\n")[0]
        assert "state.filter = defaultFilter();" in choose


class TestAModelNameInTheSidebarFiltersTheList:
    """A small extra: clicking a model's name in the spending sidebar lists
    only the conversations that used it."""

    def _chat(self) -> str:
        return _rendered_chat()

    def _usage(self) -> str:
        return self._chat().split("async function loadUsage() {")[1].split("\n}\n")[0]

    def test_the_name_is_a_real_button_underneath(self):
        usage = self._usage()
        assert 'nameEl.className = "spend-model-filter";' in usage
        assert 'nameEl.setAttribute("role", "button");' in usage
        assert "nameEl.tabIndex = 0;" in usage
        assert "filterByModel(model)" in usage
        # Still text, not markup: a model name never becomes HTML.
        assert "nameEl.textContent = model;" in usage

    def test_its_tooltip_says_it_filters(self):
        assert "nameEl.title = `Click to list only the conversations that used ${model}`;" in self._usage()

    def test_it_looks_like_plain_text_until_reached(self):
        page = self._chat()
        rule = page.split(".spend-model-filter {")[1].split("}")[0]
        for look in ("color", "text-decoration", "background", "font-weight"):
            assert look not in rule, look
        hover = page.split(".spend-model-filter:hover, .spend-model-filter:focus-visible {")[1].split("}")[0]
        assert "var(--hover-bg)" in hover

    def test_a_click_replaces_the_filter_rather_than_adding_to_it(self):
        script = self._chat().split("function filterByModel(model) {")[1].split("\n}\n")[0]
        assert "const filter = defaultFilter();" in script
        assert "state.filter = filter;" in script
