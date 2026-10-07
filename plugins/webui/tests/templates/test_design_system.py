"""Tests for plugins/webui/src/templates/_design-system.html and the parts every page shares.

The colours, spacing and controls every page is built from, and the rules that
keep each page using them rather than drawing its own: keyboard access, one
combobox, one height for controls on a row, what ``hidden`` means, and the tab icon.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from plugins.webui.tests.helpers import (
    WEBUI_SRC,
    _rendered_chat,
    _rendered_template,
)

# Every test here runs as two fake professors with no passphrase set; see conftest.py.
pytestmark = pytest.mark.usefixtures("_configured_professors", "_no_passphrase")


class TestOneDesignSystem:
    """The values are decided in one place, and the pages agree.

    They did not: four pages each held their own copy and three had drifted,
    with the Settings page painting itself a different orange from the page it
    opens inside — under a comment promising the two were identical.
    """

    TEMPLATES = ("chat.html", "settings.html", "shared_settings.html", "unlock.html")

    def _dir(self):

        return WEBUI_SRC / "templates"

    def _source(self, name):
        return (self._dir() / name).read_text()

    def _rendered(self, name):
        from fastapi.templating import Jinja2Templates

        env = Jinja2Templates(directory=str(self._dir())).env
        return env.get_template(name).render(
            request=None, error=None, can_reveal=True, default_sampling={}
        )

    def test_every_page_takes_its_values_from_the_one_place(self):
        for name in self.TEMPLATES:
            assert '{% include "_design-system.html" %}' in self._source(name), name

    def test_no_page_declares_its_own(self):
        """A second copy is how the drift started."""
        for name in self.TEMPLATES:
            assert "--orange:" not in self._source(name), (
                f"{name} declares a colour of its own instead of using the shared one"
            )

    def test_the_pages_agree_on_the_accent(self):
        accents = {name: self._rendered(name).count("--orange: #E77500")
                   for name in self.TEMPLATES}
        assert all(n == 1 for n in accents.values()), accents

    def test_type_sizes_come_from_the_scale(self):
        """Fourteen ad-hoc sizes, several a fraction of a pixel apart."""
        import re

        for name in self.TEMPLATES:
            css = self._source(name).split("</style>")[0]
            literals = re.findall(r"font-size:\s*([0-9.]+)rem", css)
            assert not literals, f"{name} still sizes text by hand: {literals}"

    def test_shapes_and_faces_come_from_the_scale_too(self):
        """Eight corner radii, where 20px and 999px both meant "fully round"."""
        import re

        for name in self.TEMPLATES:
            page = self._source(name)
            radii = re.findall(r"border-radius:\s*(\d+)px", page)
            assert not radii, f"{name} still rounds corners by hand: {radii}"
            assert "ui-monospace" not in page, f"{name} writes out a font stack"

    def test_nothing_is_smaller_than_twelve_pixels(self):
        """It went down to 9.9px, on a badge, and 11.2px on the sidebar."""
        import re

        system = self._source("_design-system.html")
        steps = [float(v) for v in re.findall(r"--text-[a-z]+:\s*([0-9.]+)rem", system)]
        assert steps, "no type scale found"
        assert min(steps) * 16 >= 12, f"the scale reaches {min(steps) * 16}px"


class TestTheInterfaceCanBeReachedWithoutAMouse:
    def _chat(self):
        return _rendered_chat()

    def test_there_is_a_focus_style_at_all(self):

        system = (WEBUI_SRC / "templates"
                  / "_design-system.html").read_text()
        assert ":focus-visible" in system

    def test_the_focus_ring_can_be_seen_on_every_surface(self):
        """Princeton's orange makes 3:1 on a panel and 2.78:1 on the page.

        The transcript sits on the page, so the ring is drawn in a darkened
        orange that clears both. This is the arithmetic that caught it.
        """
        import re

        css = (WEBUI_SRC / "templates"
               / "_design-system.html").read_text()

        def values(block_start):
            segment = css.split(block_start)[1].split("}")[0]
            return dict(re.findall(r"(--[a-z-]+):\s*(#[0-9a-fA-F]{6})", segment))

        light = values(":root {")
        dark = {**light, **values('[data-theme="dark"] {')}

        def luminance(colour):
            channels = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            adjusted = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
                        for c in channels]
            return 0.2126 * adjusted[0] + 0.7152 * adjusted[1] + 0.0722 * adjusted[2]

        for theme in (light, dark):
            for surface in ("--bg", "--panel-bg"):
                pair = sorted((luminance(theme["--focus-ring"]), luminance(theme[surface])),
                              reverse=True)
                ratio = (pair[0] + 0.05) / (pair[1] + 0.05)
                assert ratio >= 3.0, f"the ring is {ratio:.2f}:1 against {surface}"

    def test_a_button_label_holds_the_line_that_was_chosen_for_it(self):
        """Words on a button are words, so WCAG asks 4.5:1 of them rather than
        the 3:1 it asks of a border or an icon. White on the light theme's
        orange reaches 3.08:1, and that is a deliberate exception — satisfying
        4.5:1 needs either a darker orange or dark writing, and the maintainer
        looked at both and kept the colour.

        So this holds the line actually chosen, 3:1, rather than the one being
        set aside. It still catches the thing worth catching: an orange or a
        label picked later that fails even the bar for a plain border. The
        reason for the exception is written beside the token itself.
        """
        import re

        css = (WEBUI_SRC / "templates"
               / "_design-system.html").read_text()

        def values(block_start):
            segment = css.split(block_start)[1].split("}")[0]
            return dict(re.findall(r"(--[a-z-]+):\s*(#[0-9a-fA-F]{6})", segment))

        light = values(":root {")
        dark = {**light, **values('[data-theme="dark"] {')}

        def luminance(colour):
            channels = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
            adjusted = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
                        for c in channels]
            return 0.2126 * adjusted[0] + 0.7152 * adjusted[1] + 0.0722 * adjusted[2]

        for name, theme in (("light", light), ("dark", dark)):
            for surface in ("--orange", "--orange-hover"):
                pair = sorted((luminance(theme["--on-orange"]), luminance(theme[surface])),
                              reverse=True)
                ratio = (pair[0] + 0.05) / (pair[1] + 0.05)
                assert ratio >= 3.0, (
                    f"{name}: the label is {ratio:.2f}:1 on {surface} "
                    f"({theme['--on-orange']} on {theme[surface]}) — below the "
                    "3:1 that even a border is held to")

    def test_controls_that_appear_on_hover_appear_on_focus_too(self):
        """They stayed in the tab order while invisible."""
        chat = self._chat()
        assert ".conv-item:focus-within .conv-menu-btn" in chat
        assert ".msg:focus-within .msg-actions" in chat

    def test_the_smallest_controls_can_be_hit(self):
        """The two at 20x20 were the message actions and the menu holding Delete."""
        import re

        chat = self._chat()
        for selector in (".conv-menu-btn {", ".msg-action-btn {"):
            rule = chat.split(selector)[1].split("}")[0]
            size = int(re.search(r"width:\s*(\d+)px", rule).group(1))
            assert size >= 28, f"{selector} is still {size}px"
        for selector in (".conv-menu-btn::after {", ".msg-action-btn::after {"):
            rule = chat.split(selector)[1].split("}")[0]
            assert "inset:" in rule, f"{selector} has no expanded pointer target"

    def test_the_menu_button_does_not_reach_back_over_the_title(self):
        """Its expanded target is invisible, and invisible is still clickable.

        Eight pixels of it used to lie across the end of the conversation's
        name. A click there landed on the button, which stops the event, so the
        row never heard it: the words looked clickable and opened a menu.
        """
        import re

        chat = self._chat()
        rule = chat.split(".conv-menu-btn::after {")[1].split("}")[0]
        insets = re.search(r"inset:\s*([^;]+);", rule).group(1).split()
        # top right bottom left, in the order CSS reads them.
        assert len(insets) == 4, f"expected four sides, got {insets}"
        top, right, bottom, left = insets
        assert left == "0", f"it still grows {left} into the title"
        # Still a fingertip tall, out of a button drawn at 28px.
        assert top == bottom == "-8px"
        assert 28 + 8 + 8 >= 44

    def test_the_page_says_what_it_is(self):
        """Eight second-level headings and no first-level one."""
        chat = self._chat()
        assert "<h1" in chat

    def test_it_has_the_regions_a_screen_reader_moves_between(self):
        chat = self._chat()
        for tag in ("<header", "<nav", "<main", "<aside"):
            assert tag in chat, tag

    def test_the_message_box_is_labelled_and_says_how_to_send(self):
        """Enter sends and Shift+Enter does not; that was written nowhere."""
        chat = self._chat()
        assert 'for="input"' in chat
        assert "Shift+Enter" in chat

    def test_movement_stops_for_anyone_who_asked_for_less(self):

        system = (WEBUI_SRC / "templates"
                  / "_design-system.html").read_text()
        assert "prefers-reduced-motion" in system


class TestTheTranscriptIsBuiltForReading:
    def _chat(self):
        return _rendered_chat()

    def test_the_measure_is_capped(self):
        """It was 70% of an uncapped column: ~190 characters on a wide screen."""
        chat = self._chat()
        rule = chat.split(".msg {")[1].split("}")[0]
        assert "var(--measure)" in rule
        assert "70%" not in rule

    def test_the_line_spacing_suits_the_scripts_it_renders(self):
        """Transcripts here carry thousands of characters of Hangul and kana,
        which fill their em box and need more room than Latin."""
        chat = self._chat()
        rule = chat.split(".msg-body {")[1].split("}")[0]
        assert "line-height: 1.7" in rule

    def test_what_the_model_wrote_is_set_in_the_reading_face(self):
        chat = self._chat()
        assert "font-family: var(--font-reading)" in chat.split(".msg-body {")[1].split("}")[0]

    def test_the_reading_face_starts_as_the_serif(self):
        """The choice exists; the serif is what a page of prose wants until
        somebody says otherwise."""

        system = (WEBUI_SRC / "templates"
                  / "_design-system.html").read_text()
        assert "--font-reading: var(--font-text);" in system
        assert '[data-reading="sans"] { --font-reading: var(--font-ui); }' in system

    def test_no_text_sits_on_the_orange(self):
        """White on it is 2.63:1 at the value this page used to carry."""
        chat = self._chat()
        assert "background: var(--orange); color: white" not in chat

    def test_the_orange_still_marks_your_own_turns(self):
        """As a rule beside the words rather than a block behind them."""
        chat = self._chat()
        assert ".msg.user .msg-body { border-left-color: var(--orange); }" in chat

    def test_the_reading_face_can_set_the_scripts_this_sandbox_sees(self):
        """A stack that stopped at Latin would leave the browser to guess."""

        system = (WEBUI_SRC / "templates"
                  / "_design-system.html").read_text()
        stack = system.split("--font-text:")[1].split(";")[0]
        assert "Mincho" in stack, "no Japanese face"
        assert "Songti" in stack or "SimSun" in stack, "no Chinese face"
        assert "Myungjo" in stack or "Myeongjo" in stack or "Batang" in stack, "no Korean face"


class TestControlsSitProperlyTogether:
    def _chat(self):
        return _rendered_chat()

    def _rule(self, selector):
        return self._chat().split(selector)[1].split("}")[0]

    def test_the_chevron_is_the_size_of_the_other_icons(self):
        """It had no size of its own, so it filled its whole button."""
        import re

        size = int(re.search(r"width:\s*(\d+)px", self._rule(".combobox-toggle svg {")).group(1))
        assert size == 11, f"the chevron is {size}px against 11px elsewhere"

    def test_the_new_conversation_mark_has_room_for_its_plus(self):
        """The drawing is a square with a plus inside it; at 12px the plus
        itself was about five pixels across."""
        import re

        drawing = int(re.search(r"width:\s*(\d+)px", self._rule(".plus-btn svg {")).group(1))
        button = int(re.search(r"width:\s*(\d+)px", self._rule(".plus-btn {")).group(1))
        assert drawing >= 18, f"the mark is {drawing}px"
        assert button > drawing, "the drawing would touch the button's edge"

    def test_the_composer_buttons_are_centred_against_the_box(self):
        """They are shorter than it, so flush-to-the-bottom left all the slack
        above them and the row read as uneven. The margin is what centres it:
        if either number moves without the other, this says so."""
        import re

        chat = self._chat()
        box = float(re.search(r"height:\s*([0-9.]+)rem",
                              chat.split("#composer textarea {")[1].split("}")[0]).group(1))
        rule = chat.split("#composer .icon-btn, #composer #send-btn {")[1].split("}")[0]
        button = float(re.search(r"height:\s*([0-9.]+)rem", rule).group(1))
        below = float(re.search(r"margin-bottom:\s*([0-9.]+)rem", rule).group(1))
        assert button < box, "the button is not shorter than the box"
        above = box - button - below
        assert abs(above - below) < 0.001, (
            f"{above:.2f}rem above and {below:.2f}rem below — not centred"
        )

    def test_the_row_stays_bottom_aligned_as_the_box_grows(self):
        """The box grows with what is pasted into it. Centring the row itself
        would float the buttons into the middle of a tall box."""
        chat = self._chat()
        assert "align-items: flex-end" in chat.split("#composer {")[1].split("}")[0]

    def test_the_settings_popover_fits_the_sentence_inside_it(self):
        """It holds the conversation instructions, whose own example runs to
        about fifty characters."""
        import re

        width = self._rule(".sampling-options-popover {")
        rem = float(re.search(r"width:\s*([0-9.]+)rem", width).group(1))
        assert rem >= 24, f"{rem}rem is narrower than the example text it shows"

    def test_a_floating_panel_can_be_told_from_what_it_covers(self):
        """The conversation menu opens over the sidebar, and both were the same
        colour with a faint shadow between them."""
        for selector in (".conv-menu {", ".combobox-list {", ".model-add-popover {",
                         ".sampling-options-popover {", ".action-picker {"):
            rule = self._rule(selector)
            assert "var(--surface-raised)" in rule, selector
            assert "var(--shadow-raised)" in rule, selector

    def test_the_raised_surface_actually_differs_in_the_dark_theme(self):
        """In the light theme a shadow separates white from white; in the dark
        theme there is no white, so the surface itself has to lift."""
        import re

        system = (WEBUI_SRC / "templates"
                  / "_design-system.html").read_text()
        dark = system.split('[data-theme="dark"]')[1].split("}")[0]
        raised = re.search(r"--surface-raised:\s*(#[0-9a-fA-F]{6})", dark).group(1)
        panel = re.search(r"--panel-bg:\s*(#[0-9a-fA-F]{6})", dark).group(1)
        assert raised.lower() != panel.lower(), "a menu is the colour of what it covers"


class TestChoosingHowRepliesAreSet:
    """Serif or sans, remembered, and only for what was written."""

    def _chat(self):
        return _rendered_chat()

    def test_there_is_a_control_for_it(self):
        chat = self._chat()
        assert 'id="reading-face-btn"' in chat

    def test_the_choice_is_remembered(self):
        """Like the light/dark choice, and stored the same way."""
        chat = self._chat()
        assert 'localStorage.setItem("reading-face"' in chat
        assert 'localStorage.getItem("reading-face")' in chat

    def test_the_button_samples_the_face_you_would_get(self):
        """Not the one you are already reading. A button showing what you
        already have says nothing about what pressing it does."""
        chat = self._chat()
        # Reading the serif, the sample is the sans; the other way by default.
        assert '[data-reading="serif"] .reading-face-mark { font-family: var(--font-ui); }' in chat
        assert ".reading-face-mark { font-family: var(--font-text); }" in chat

    def test_it_says_which_way_it_will_switch(self):
        chat = self._chat()
        assert "Read replies in a serif face" in chat
        assert "Read replies in a sans-serif face" in chat

    def test_the_interface_does_not_follow_the_choice(self):
        """Only what was written changes. If the interface followed too, the
        two would stop being distinguishable, which was the point of having
        two faces."""

        system = (WEBUI_SRC / "templates"
                  / "_design-system.html").read_text()
        sans = system.split('[data-reading="sans"]')[1].split("}")[0]
        assert "--font-reading" in sans
        assert "--font-ui:" not in sans, "the choice redefines the interface's own face"


class TestTheFollowUpFixesStay:
    """Six things that were reported after a first attempt at each."""

    def _source(self, name):

        return (WEBUI_SRC / "templates" / name).read_text()

    def test_hiding_the_settings_bar_beats_the_rule_that_shows_it(self):
        """Setting the attribute was not enough: #topbar sets display, and an
        author rule outranks the browser's meaning for [hidden].

        Read rendered, not as source: the bar moved into _forms.html when the
        four pages stopped each keeping their own copy of it.
        """
        page = _rendered_template("settings.html")
        assert "#topbar[hidden] { display: none; }" in page
        # Both rules are one selector each, so neither outranks the other and
        # source order decides. The hiding one has to come first.
        assert page.index("#topbar[hidden]") < re.search(r"#topbar\s*\{", page).start()

    def test_a_menu_row_can_be_seen_under_the_pointer(self):
        """The ordinary hover is 1.02:1 against the raised surface a menu sits
        on, so a menu appeared to have no hover at all."""
        chat = self._source("chat.html")
        assert "var(--hover-raised)" in chat.split(".conv-menu-option:hover")[1].split("}")[0]
        assert "var(--border-raised)" in chat.split(".conv-menu-divider {")[1].split("}")[0]

    def test_the_raised_hover_and_border_differ_from_the_raised_surface(self):
        import re

        system = self._source("_design-system.html")
        dark = system.split('[data-theme="dark"]')[1].split("}")[0]
        values = dict(re.findall(r"(--[a-z-]+):\s*(#[0-9a-fA-F]{6})", dark))
        for key in ("--hover-raised", "--border-raised"):
            assert values[key].lower() != values["--surface-raised"].lower(), key

    def test_the_new_conversation_button_is_coloured_like_the_send_button(self):
        """Because the Send button reads: a solid orange with the interface's
        dark ink on it. Every attempt at keeping the mark orange put an orange
        shape on an orange ground."""
        chat = self._source("chat.html")
        rule = chat.split(".plus-btn {")[1].split("}")[0]
        assert "background:" not in rule, "it overrides the button colour it should take"
        assert ":hover" not in chat.split(".plus-btn {")[0].split(".plus-btn")[-1]

    def test_the_add_model_box_fits_the_example_inside_it(self):
        import re

        chat = self._source("chat.html")
        rem = float(re.search(r"width:\s*([0-9.]+)rem",
                              chat.split(".model-add-popover {")[1].split("}")[0]).group(1))
        placeholder = "openai/gpt-4o, or della:alibaba/qwen35"
        # Roughly half an em per character at the box's own size, less the
        # padding and the button beside it.
        fits = (rem * 16 - 80) / (0.8125 * 16 * 0.5)
        assert fits >= len(placeholder), f"{fits:.0f} characters of {len(placeholder)}"

    def test_the_composer_row_is_settled(self):
        """Equal heights read as uneven because the box carries an outline and
        the buttons do not. They are shorter and centred instead."""
        chat = self._source("chat.html")
        rule = chat.split("#composer .icon-btn, #composer #send-btn {")[1].split("}")[0]
        assert "margin-bottom" in rule


class TestOneComboboxForEveryPage:
    """Three pickers is where copying the markup had to stop.

    A <select> is drawn by the operating system: not the height of the fields
    around it, not the arrow used anywhere else, and a list that ignores the
    theme — a white box out of nowhere in the dark one. Each picker that stopped
    being a <select> had been another copy of the same CSS and the same handlers.
    """

    def _rendered(self, name):
        from fastapi.templating import Jinja2Templates
        directory = WEBUI_SRC / "templates"
        return Jinja2Templates(directory=str(directory)).get_template(name).render(request=None)

    def test_the_partial_exists_on_its_own(self):
        partial = (WEBUI_SRC / "templates"
                   / "_combobox.html")
        assert partial.exists()

    def test_both_pages_include_it(self):
        for page in ("chat.html", "settings.html"):
            source = (WEBUI_SRC / "templates"
                      / page).read_text()
            assert '{% include "_combobox.html" %}' in source, page

    def test_neither_page_keeps_its_own_copy(self):
        """The drift this is here to prevent."""
        for page in ("chat.html", "settings.html"):
            source = (WEBUI_SRC / "templates"
                      / page).read_text()
            assert ".combobox-list {" not in source, f"{page} has its own copy of the CSS"
            assert "function wireCombobox" not in source, f"{page} has its own copy of the JS"

    def test_the_settings_picker_is_no_longer_a_select(self):
        page = self._rendered("settings.html")
        assert '<select id="add-model-professor"' not in page
        assert 'id="add-model-professor-combobox"' in page

    def test_all_three_pickers_are_wired(self):
        chat = self._rendered("chat.html")
        settings = self._rendered("settings.html")
        assert 'wireCombobox("model")' in chat
        assert 'wireCombobox("professor")' in chat
        assert 'wireCombobox("add-model-professor")' in settings

    def test_one_prefix_finds_every_part(self):
        """Field, list, chevron and container are all found from the one name."""
        partial = self._rendered("chat.html")
        fn = partial.split("function wireCombobox")[1].split("\n}")[0]
        assert 'prefix + "-toggle-btn"' in fn
        assert "comboboxField(prefix)" in fn

    def test_dismissal_covers_every_picker_without_naming_one(self):
        page = self._rendered("settings.html")
        handler = page.split('document.addEventListener("click"')[1].split("\n});")[0]
        assert "WIRED_COMBOBOXES.forEach" in handler

    def test_the_component_still_claims_no_space_of_its_own(self):
        """The sidebar bug, now in a file two pages depend on."""
        page = self._rendered("settings.html")
        rule = page.split(".combobox { ")[1].split("}")[0]
        assert "flex:" not in rule

    def test_the_form_sends_a_netid_and_not_a_name(self):
        """The field shows 'Jeff Heller (jh43)'; the request needs 'jh43'."""
        page = self._rendered("settings.html")
        assert "const professor = addModelProfessor;" in page
        assert 'getElementById("add-model-professor").value' not in page

    def test_someone_is_chosen_to_begin_with(self):
        """A <select> shows its first option; an empty box looks broken."""
        fn = self._rendered("settings.html").split("function renderModelProfessors")[1]
        assert "if (!addModelProfessor) {" in fn.split("\n}")[0]


class TestNoPageInventsAColourItAlreadyHasATokenFor:
    """A literal where a token belongs stops following the theme.

    settings.html and shared_settings.html both hardcoded #cc6600 for
    button:hover. That is --orange-hover's *light* value; in the dark theme the
    token is #ff8f2e, deliberately lighter, because darkening an orange on a
    dark panel moves it towards the background rather than away from it. So on
    those two pages, in dark mode, hovering a button made it recede — the exact
    thing the design system's own comment says not to do.
    """

    TEMPLATES = ["chat.html", "settings.html", "shared_settings.html", "unlock.html"]

    def _template(self, name):
        return (WEBUI_SRC / "templates" / name).read_text()

    def test_no_page_repeats_a_value_the_design_system_names(self):
        tokens = self._template("_design-system.html")
        # Every colour the token layer defines, as written there.
        defined = set(re.findall(r"--[\w-]+:\s*(#[0-9A-Fa-f]{3,8})\s*;", tokens))
        assert defined, "no colours found — has the token layer moved?"
        for name in self.TEMPLATES:
            body = re.sub(r"/\*.*?\*/", "", self._template(name), flags=re.S)
            for colour in defined:
                assert colour.lower() not in body.lower(), (
                    f"{name} writes {colour} itself; the token layer already names it, "
                    "and a literal stops following the theme"
                )

    def test_the_hover_follows_the_theme_on_every_page(self):
        for name in self.TEMPLATES:
            body = self._template(name)
            # The bare element rule, not "#tabs button:hover" — a tab is not
            # an orange button and is right to hover differently.
            found = re.search(r"^\s*button:hover\s*\{([^}]*)\}", body, re.M)
            if not found:
                continue
            assert "var(--orange-hover)" in found.group(1), f"{name} does not use the token"

    def test_the_token_really_does_differ_between_themes(self):
        """If it ever stops differing, the bug above stops being possible."""
        tokens = self._template("_design-system.html")
        values = re.findall(r"--orange-hover:\s*(#[0-9A-Fa-f]+)", tokens)
        assert len(values) == 2, "expected a light value and a dark one"
        assert values[0].lower() != values[1].lower()


class TestNoRuleIsWrittenTwice:
    """Four pages each keeping their own copy is how they drifted apart.

    Before the shared partials, 39 selectors were declared in more than one
    template and 13 of those had different rules — including button:hover, where
    two pages wrote a colour literal that made them recede on hover in the dark
    theme.
    """

    TEMPLATES = ["chat.html", "settings.html", "shared_settings.html", "unlock.html"]
    PARTIALS = ["_design-system.html", "_forms.html", "_panels.html",
                "_combobox.html"]

    def _rules(self, name):
        text = (WEBUI_SRC / "templates" / name).read_text()
        css = "\n".join(re.findall(r"<style>(.*?)</style>", text, re.S))
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        out = {}
        for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
            sel = " ".join(sel.split())
            if sel and not sel.startswith("@"):
                out[sel] = " ".join(body.split())
        return out

    def test_no_page_repeats_a_rule_a_partial_already_gives_it(self):
        """A copy identical to the shared one is dead weight that can drift."""
        shared = {}
        for partial in self.PARTIALS:
            shared.update(self._rules(partial))
        for name in self.TEMPLATES:
            for sel, body in self._rules(name).items():
                assert shared.get(sel) != body, (
                    f"{name} repeats '{sel}' exactly as the shared partial has it"
                )

    def test_a_page_that_overrides_says_only_what_differs(self):
        """Restating the rest is how one of them silently stops matching."""
        shared = {}
        for partial in self.PARTIALS:
            shared.update(self._rules(partial))
        for name in self.TEMPLATES:
            for sel, body in self._rules(name).items():
                if sel not in shared:
                    continue
                mine = {d.split(":")[0].strip() for d in body.split(";") if ":" in d}
                theirs = {d.split(":")[0].strip(): d.split(":", 1)[1].strip()
                          for d in shared[sel].split(";") if ":" in d}
                repeated = {
                    p for p in mine & set(theirs)
                    if [d.split(":", 1)[1].strip() for d in body.split(";")
                        if d.split(":")[0].strip() == p] == [theirs[p]]
                }
                assert not repeated, (
                    f"{name}'s '{sel}' restates {sorted(repeated)} unchanged from the partial"
                )

    def test_the_two_settings_pages_no_longer_share_a_stylesheet(self):
        """shared_settings.html was 68% a copy of settings.html."""
        a, b = self._rules("settings.html"), self._rules("shared_settings.html")
        # A selector may legitimately appear in both when the pages genuinely
        # differ — #page is 720px on one and 1040px on the other. What must not
        # appear in both is the same rule written out twice.
        copies = {s for s in set(a) & set(b) if a[s] == b[s]}
        assert not copies, f"still written out in both: {sorted(copies)}"

    def test_artwork_used_twice_is_drawn_once(self):
        """1,842 characters of path data were repeated across the templates."""
        from collections import Counter
        seen = Counter()
        for name in self.TEMPLATES:
            text = (WEBUI_SRC / "templates" / name).read_text()
            seen.update(re.findall(r'<path d="([^"]{60,})"', text))
        repeated = {d[:40]: n for d, n in seen.items() if n > 1}
        assert not repeated, f"path data still repeated: {list(repeated)}"

    def test_an_icon_in_the_partial_is_never_also_pasted_inline(self):
        """Counting repeats is not enough: one inline copy beside the macro
        calls repeats nothing and still leaves two drawings to keep in step."""
        icons = (WEBUI_SRC / "templates"
                 / "_icons.html").read_text()
        drawn = re.findall(r'<path d="([^"]{60,})"', icons)
        assert drawn, "no artwork found — has _icons.html moved?"
        for name in self.TEMPLATES:
            text = (WEBUI_SRC / "templates" / name).read_text()
            for d in drawn:
                assert d not in text, (
                    f"{name} draws an icon inline that _icons.html already has; "
                    "call the macro instead"
                )

    def test_the_panels_are_only_given_to_pages_built_from_them(self):
        """The chat page uses none of them; shipping it the rules is the same
        waste as the duplication, moved rather than removed."""
        for name in self.TEMPLATES:
            source = (WEBUI_SRC / "templates" / name).read_text()
            wants = name in ("settings.html", "shared_settings.html")
            assert ('{% include "_panels.html" %}' in source) is wants, name


class TestControlsOnOneRowAreOneHeight:
    """A field, a menu and a button are each given their own padding, and a
    browser adds its own idea of how tall a menu should be on top — so three
    of them side by side came out three heights."""

    def _partial(self, name):

        return (WEBUI_SRC / "templates" / name).read_text()

    def test_one_height_is_defined_once(self):
        assert "--control-height:" in self._partial("_design-system.html")

    def test_and_applied_to_everything_sharing_a_row(self):
        css = self._partial("_forms.html")
        rule = css[css.index(":is(.inline-fields, .field-set, form.inline)"):]
        rule = rule[:rule.index("}")]
        for control in ("input", "select", "button"):
            assert control in rule, control
        assert "min-height: var(--control-height)" in rule

    def test_only_in_a_row(self):
        """The chat page's icon buttons come through the same stylesheet and
        are not fields; stretching them would make them look like fields."""
        css = self._partial("_forms.html")
        at = css.index("min-height: var(--control-height)")
        selector = css[css.rindex("\n", 0, css.rindex("{", 0, at)):at]
        assert ".inline-fields" in selector or ".field-set" in selector

    def test_the_file_fields_button_is_the_sandboxs_own(self):
        """It was the one control still drawn by the browser."""
        css = self._partial("_forms.html")
        assert "input[type=file]::file-selector-button" in css
        rule = css[css.index("input[type=file]::file-selector-button"):]
        rule = rule[:rule.index("}")]
        # The same clothes as button.secondary beside it.
        assert "border: 1px solid var(--border)" in rule
        assert "border-radius: var(--radius-md)" in rule
        assert "padding: 0.45rem 0.8rem" in rule

    def test_the_file_field_is_still_a_real_file_field(self):
        """Styled, not replaced by a label dressed as a button — the real
        control keeps its keyboard behaviour and its focus ring."""
        chat = _rendered_chat()
        assert 'replacement.type = "file"' in chat
        # Styled, not hidden behind something else that clicks it for you.
        css = self._partial("_forms.html")
        rule = css[css.index("input[type=file]::file-selector-button"):]
        assert "display: none" not in rule[:rule.index("}")]
        assert "input[type=file] { display: none" not in css


class TestTheTabIcon:
    """The mark reaches the browser's tab, on every page and both applications.

    A browser asks for /favicon.ico by itself, whether or not a page mentions
    one, so the address has to answer even where no template was involved.
    """

    def test_the_icon_is_served(self, client):
        resp = client.get("/favicon.ico")
        assert resp.status_code == 200
        # A real icon file, not an HTML error page dressed as one.
        assert resp.content[:4] == b"\x00\x00\x01\x00"

    def test_the_icon_needs_no_passphrase(self, client):
        """The unlock screen is itself a page in a tab, and an icon is no secret."""
        assert client.get("/favicon.ico").status_code == 200

    def test_the_setup_pages_show_it_too(self):
        """Two applications, one mark — otherwise setup looks like other software."""
        setup_web = sys.modules["_pu_webui_setup_web"]
        setup_client = TestClient(setup_web.create_setup_app(lambda: None))
        assert setup_client.get("/favicon.ico").status_code == 200

    def test_the_icon_holds_the_three_sizes_a_browser_picks_between(self):
        """Read from the file's own table of contents rather than with an
        image library, because nothing here depends on one."""
        branding = sys.modules["_pu_webui_branding"]
        raw = Path(branding.FAVICON_PATH).read_bytes()

        # An .ico opens with a 6-byte header whose last two bytes count the
        # drawings inside, then one 16-byte entry per drawing beginning with
        # its width and height.
        count = int.from_bytes(raw[4:6], "little")
        sizes = sorted((raw[6 + i * 16], raw[7 + i * 16]) for i in range(count))
        assert sizes == [(16, 16), (32, 32), (48, 48)]

    def test_every_page_names_it(self):
        for name in ("chat.html", "unlock.html", "settings.html",
                     "shared_settings.html", "setup.html"):
            page = _rendered_template(name)
            assert 'href="/favicon.ico"' in page, name
            assert 'type="image/svg+xml"' in page, name

    def test_the_drawn_version_carries_a_ground_the_header_mark_does_not(self):
        """In the artwork the tiger is the page showing through. A tab bar is
        not ours to colour, and a dark one turned the tiger dark."""
        page = _rendered_template("chat.html")
        link = page[page.index('type="image/svg+xml"'):]
        link = link[:link.index(">")]
        assert "%23ffffff" in link
        # Same artwork as the header's, not a second drawing of it.
        assert "%23f58025" in link


class TestHiddenMeansHidden:
    """An element's own ``display`` beats the ``hidden`` attribute.

    So a rule that gives something ``display: flex`` quietly keeps it on
    screen however often the script hides it. That is how the update notice
    came to sit above every conversation, with nothing in it and a "Not now"
    that did nothing: the words are only written in when there is an update,
    and hiding it had no effect. The Updates button and the conversation
    folder button were stuck showing for the same reason.

    Checked across every page, for everything that starts hidden or is hidden
    by id from a script: if a rule of its own gives it a display, a
    ``[hidden]`` rule of its own has to take that back.
    """

    PAGES = ("chat.html", "settings.html", "setup.html", "unlock.html",
             "shared_settings.html")

    @staticmethod
    def _rules(page: str) -> list[tuple[list[str], str, bool]]:
        """Every one-part selector's #id/.class names, its display, and
        whether it is a [hidden] rule. A selector with a descendant or a
        pseudo-class is left out: it applies only somewhere in particular."""
        import re

        css = "\n".join(re.findall(r"<style[^>]*>(.*?)</style>", page, re.S))
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        rules = []
        for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
            shown = re.search(r"display:\s*([a-z-]+)", body)
            for selector in selectors.split(","):
                selector = selector.strip()
                if not selector or " " in selector or ">" in selector or ":" in selector:
                    continue
                names = re.findall(r"[#.][\w-]+", selector)
                if names:
                    rules.append((names, shown.group(1) if shown else "",
                                  "[hidden]" in selector))
        return rules

    @staticmethod
    def _hideable(page: str) -> list[tuple[str, set[str]]]:
        """Each element that starts hidden or is hidden by id, as a
        description and the #id/.class names it answers to."""
        import re

        by_script = set(re.findall(r'getElementById\("([\w-]+)"\)\.hidden\s*=', page))
        found = []
        for m in re.finditer(r"<([a-z][\w-]*)(\s[^>]*)?>", page):
            attrs = m.group(2) or ""
            ident = re.search(r'\bid="([^"]+)"', attrs)
            classes = re.search(r'\bclass="([^"]+)"', attrs)
            starts_hidden = re.search(r"\shidden(?=[\s>=/]|$)", attrs) is not None
            if not starts_hidden and not (ident and ident.group(1) in by_script):
                continue
            names = {"." + c for c in (classes.group(1).split() if classes else [])}
            if ident:
                names.add("#" + ident.group(1))
            found.append((m.group(0)[:80], names))
        return found

    @pytest.mark.parametrize("name", PAGES)
    def test_nothing_hidden_is_kept_on_screen_by_its_own_display(self, name):
        page = _rendered_template(name)
        rules = self._rules(page)
        stuck = []
        for element, names in self._hideable(page):
            mine = [r for r in rules if all(n in names for n in r[0])]
            shows = any(d and d != "none" and not hidden for _, d, hidden in mine)
            takes_back = any(hidden and d == "none" for _, d, hidden in mine)
            if shows and not takes_back:
                stuck.append(element)
        assert not stuck, f"hidden has no effect on: {stuck}"

    def test_it_would_catch_the_update_notice(self):
        """The check itself, against the rule that went wrong."""
        page = ('<style>#update-notice { display: flex; }</style>'
                '<div id="update-notice" hidden></div>')
        rules = self._rules(page)
        ((_, names),) = self._hideable(page)
        mine = [r for r in rules if all(n in names for n in r[0])]
        assert any(d == "flex" for _, d, _h in mine)
        assert not any(h for _n, _d, h in mine)
