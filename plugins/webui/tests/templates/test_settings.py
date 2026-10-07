"""Tests for plugins/webui/src/templates/settings.html: the settings page as the browser receives it."""

from __future__ import annotations

import re
import sys

import pytest

from plugins.webui.tests.helpers import (
    WEBUI_SRC,
    _code_only,
    _rendered_template,
)


class TestSharedSettingsLink:
    """How the editor is reached from the Settings modal."""

    def _settings_page(self):

        from fastapi.templating import Jinja2Templates

        directory = WEBUI_SRC / "templates"
        return Jinja2Templates(directory=str(directory)).get_template(
            "settings.html"
        ).render(request=None)

    def test_it_opens_in_its_own_tab(self):
        """It is a long list to work through; losing it by leaving the modal
        would mean starting again.

        Read as "the link to that page carries target=_blank" rather than as
        one exact string, so that restyling the link does not read as breaking
        it — this failed once because a text-decoration was added between the
        two halves it was matching.
        """
        page = self._settings_page()
        link = page[page.index('<a href="/shared-settings"'):]
        assert 'target="_blank"' in link[:link.index(">")]

    def test_the_new_tab_cannot_reach_back_into_this_one(self):
        page = self._settings_page()
        link = page[page.index('href="/shared-settings"'):]
        assert 'rel="noopener"' in link[:200]

    def test_the_button_carries_the_new_tab_icon(self):
        page = self._settings_page()
        assert "external-icon" in page

    def test_and_says_so_for_a_screen_reader(self):
        """An icon alone tells someone using a screen reader nothing."""
        page = self._settings_page()
        assert "(opens in a new tab)" in page


class TestTheUsageFolderIsAskedForBesideThePerson:
    """It used to be a section of its own, where a label and a netID were both
    typed for one person who was already listed a few inches above."""

    def _settings(self):

        return (WEBUI_SRC / "templates" / "settings.html").read_text()

    def test_there_is_no_separate_sources_section(self):
        page = self._settings()
        assert 'id="section-external_sources"' not in page
        assert 'id="add-source-form"' not in page

    def test_the_folder_is_asked_for_in_the_persons_own_row(self):
        page = self._settings()
        assert 'data-usage-for="${p.netid}"' in page
        row_at = page.index('data-usage-for="${p.netid}"')
        list_at = page.index('const list = document.getElementById("professors-list")')
        assert row_at > list_at, "the folder is not being drawn with the people"

    def test_nobody_types_a_label_or_a_netid_for_it(self):
        """Both were already known: it is that person's row."""
        page = self._settings()
        assert 'id="source-label"' not in page
        assert 'id="source-professor"' not in page

    def test_both_modes_are_offered_and_explained_where_they_are_chosen(self):
        page = self._settings()
        assert 'value="read-only"' in page
        assert 'value="shared-write"' in page
        assert "never changes it" in page

    def test_the_folder_is_said_to_hold_conversations_too(self):
        """It stopped being only about spending when conversations began
        following the same setting."""
        page = self._settings()
        note = page[page.index('data-usage-for="${p.netid}"'):]
        assert "conversations" in note[:note.index("</details>")]

    def test_a_folder_can_be_given_when_somebody_is_first_added(self):
        """Otherwise it is: add them, find them in the list, open a section,
        fill in one box — for something known at the moment they were added."""
        page = self._settings()
        form = page[page.index('id="add-professor-form"'):]
        form = form[:form.index("</form>")]
        assert 'id="prof-usage-path"' in form
        assert 'id="prof-usage-mode"' in form

    def test_that_folder_is_optional(self):
        """Most people have none, and a required box would stop them adding
        anybody at all."""
        page = self._settings()
        form = page[page.index('id="add-professor-form"'):]
        form = form[:form.index("</form>")]
        path_field = form[form.index('id="prof-usage-path"'):]
        assert "required" not in path_field[:path_field.index(">")]

    def test_it_is_only_sent_when_one_was_typed(self):
        """An empty box must not record an empty folder against them."""
        page = self._settings()
        assert "if (usagePath) {" in page

    def test_the_folder_is_explained_above_the_boxes_for_it(self):
        """Read as "something explains it first", not as one id or one
        sentence — the wording here is the maintainer's and moves."""
        import re

        page = self._settings()
        form = page[page.index('id="add-professor-form"'):]
        fields_at = form.index('id="prof-usage-path"')
        paragraphs = [m for m in re.finditer(r"<p[^>]*>(.*?)</p>", form[:fields_at], re.S)]
        assert any("shared folder" in m.group(1).lower() for m in paragraphs), (
            "nothing above the boxes says what a shared folder is for")

    def test_the_clear_button_says_where_the_work_goes_instead(self):
        """"Stop reading it" said what stops, not what then happens."""
        page = self._settings()
        assert "Revert to data folder" in page
        assert "Stop reading it" not in page

    def test_the_installations_own_name_is_still_shown(self):
        """It is what shared-write calls the files it writes."""
        assert 'id="source-id"' in self._settings()

    def test_the_browse_buttons_are_drawn_after_the_rows_holding_them(self):
        """One of them is now inside a row drawn once per person, so showing
        them before the rows exist leaves that one hidden for good."""
        page = self._settings()
        # Measured from inside loadSettings, since the name of each of these
        # also appears earlier as the function that defines it.
        loading = page[page.index("async function loadSettings()"):]
        assert loading.index("showBrowseButtons(data.can_browse)") > loading.index(
            "renderProfessors(data)")

    def test_a_browse_button_with_no_id_to_point_at_finds_its_own_box(self):
        """Rows repeat, so the box beside a button in one cannot have an id."""
        page = self._settings()
        assert 'button.closest(".field-set").querySelector("input")' in page
        assert 'class="field-set"' in page


class TestTheProfessorRowsLineUp:
    """What acts on a row is held against the name it acts on."""

    def _page(self):

        return (WEBUI_SRC / "templates" / "settings.html").read_text()

    def _partial(self, name):

        return (WEBUI_SRC / "templates" / name).read_text()

    def test_the_badges_and_the_button_are_one_group(self):
        page = self._page()
        row = page[page.index('<div class="main">'):page.index("list.appendChild(row)")]
        group_at = row.index('<div class="trailing">')
        assert group_at < row.index("data-remove=")
        assert row.index("badge can") > group_at

    def test_that_group_is_held_against_the_first_line_not_the_row(self):
        """A row grows when its folded sections are opened; the name does not
        move, so neither should these."""
        css = self._partial("_forms.html")
        assert ".row:has(> .trailing) { align-items: flex-start; }" in css
        trailing = css[css.index(".row > .trailing {"):]
        assert "align-items: center" in trailing[:trailing.index("}")]
        assert "min-height" in trailing[:trailing.index("}")]

    def test_what_is_folded_away_spans_the_row(self):
        """The badges sit against the name on the line above, so there is
        nothing beside these to stop short of."""
        page = self._page()
        row = page[page.index('<div class="main">'):page.index("list.appendChild(row)")]
        assert '<div class="expandables">' in row
        # Out of .main, or it would still be as wide as .main is.
        assert row.index('class="expandables"') > row.index("</div>")
        css = self._partial("_forms.html")
        assert ".row:has(> .expandables) { flex-wrap: wrap; }" in css
        assert "flex-basis: 100%" in css[css.index(".row > .expandables {"):]

    def test_a_paragraph_after_a_field_is_given_room(self):
        """Set close, it reads as the label for the box below it, which is the
        opposite of what it says."""
        css = self._partial("_forms.html")
        rule = css[css.index(".card :is(input, select"):]
        rule = rule[:rule.index("}")]
        assert "margin-top: var(--space-6)" in rule

    def test_a_block_of_toml_is_given_the_same_room_as_a_field(self):
        """Two of them sit under Alternate AI endpoints, each followed by a
        paragraph that was reading as its caption."""
        css = self._partial("_forms.html")
        # Measured forward from the rule, not from the top of the file: the
        # same declaration appears in more than one rule, and searching from
        # the top found whichever came first.
        at = css.index(".card :is(input, select")
        selectors = css[at:css.index("margin-top: var(--space-6)", at)]
        assert ".snippet" in selectors

    def test_the_two_blocks_under_endpoints_are_each_followed_by_one(self):
        """The rule only helps if these are still shaped the way it matches."""
        import re

        page = self._page()
        card = page[page.index('id="section-endpoints"'):]
        card = card[:card.index('id="section-models"')]
        assert len(re.findall(r'</div>\s*<p class="hint"', card)) >= 2

    def test_both_keys_are_asked_for_on_one_line(self):
        page = self._page()
        form = page[page.index('data-kind="keys"'):]
        form = form[:form.index("</form>")]
        assert 'name="key"' in form and 'name="backup_key"' in form
        assert form.count("<button type=\"submit\"") == 1, "two Saves for one line"

    def test_a_blank_key_box_leaves_that_key_alone(self):
        """On a form holding both, "blank clears it" would wipe a backup key
        every time somebody changed only the primary."""
        page = self._page()
        handler = page[page.index('form[data-kind=keys]'):
                       page.index("button[data-clear-backup]")]
        assert "if (key) {" in handler
        assert "if (backup) {" in handler
        assert "(!key && !backup) return" in handler

    def test_taking_a_backup_key_away_is_its_own_button(self):
        """Blank no longer clears, so there has to be a way to clear."""
        page = self._page()
        assert "data-clear-backup=" in page
        assert 'JSON.stringify({ backup_key: null })' in page

    def test_that_button_is_only_offered_to_somebody_who_has_one(self):
        page = self._page()
        at = page.index("data-clear-backup=")
        assert "p.has_backup_key ?" in page[at:at + 200]

    def test_the_folder_and_its_mode_share_a_line_in_both_places(self):
        page = self._page()
        for where, marker in (("a professor's row", 'data-usage-for="${p.netid}"'),
                              ("the add form", 'id="add-professor-form"')):
            block = page[page.index(marker):]
            block = block[:block.index("</form>")]
            fields_at = block.index('class="inline-fields"')
            assert fields_at < block.index("What to do with it"), where
            assert "wide" in block[fields_at:block.index("What to do with it")], where

    def test_a_box_and_the_button_beside_it_are_the_same_height(self):
        """They are given different padding, so centring them leaves two
        visibly different heights sharing a middle."""
        css = self._partial("_panels.html")
        rule = css[css.index(".field-set {"):]
        assert "align-items: stretch" in rule[:rule.index("}")]

    def test_both_folded_sections_open_to_the_same_gap(self):
        """One was laid out as a row of boxes and the other as blocks, which
        added the first label's own margin in one case and absorbed it in the
        other."""
        css = self._partial("_panels.html")
        assert "details.manage > form { margin-top: var(--space-3); }" in css
        assert "details.manage > form > :first-child label:first-child" in css


class TestSectionsInSettingsAreHeadings:
    """A bold paragraph looks like a heading and is not one: nothing reading
    the page aloud, or listing its structure, can tell it apart from the
    sentence beside it."""

    def _rendered(self):

        from fastapi.templating import Jinja2Templates

        directory = WEBUI_SRC / "templates"
        return Jinja2Templates(directory=str(directory)).get_template(
            "settings.html").render(request=None)

    def _partial(self, name):

        return (WEBUI_SRC / "templates" / name).read_text()

    SECTIONS = [
        "Add a new professor",
        "Create a shared settings file for your team",
        "Adding a new API endpoint",
        "Adding a new model",
    ]

    def test_each_one_is_a_heading(self):
        page = self._rendered()
        for name in self.SECTIONS:
            assert f"<h3>{name}</h3>" in page, name

    def test_none_of_them_is_still_a_bold_paragraph(self):
        page = self._rendered()
        for name in self.SECTIONS:
            assert f"<strong>{name}</strong>" not in page, name

    def test_they_sit_under_the_card_they_belong_to(self):
        """h3 under h2, so the page's outline is the page's structure."""
        import re

        page = self._rendered()
        levels = [int(m.group(1)) for m in re.finditer(r"<h([123])\b", page)]
        assert 3 in levels and 2 in levels
        # No h3 before the first h2, which would be a section belonging to
        # nothing.
        assert levels.index(2) < levels.index(3)

    def test_emphasis_inside_a_sentence_is_left_alone(self):
        """Not every bold phrase is a heading; these are read as part of the
        sentence around them."""
        page = self._rendered()
        assert "<strong>There are no models available.</strong>" in page
        assert "<strong>Read only</strong>" in page

    def test_a_heading_is_given_the_same_air_wherever_it_appears(self):
        """The four carried inline margins of 1rem, 1rem, 0.5rem and 0.5rem —
        no two chosen together."""
        page = self._rendered()
        assert "margin-top:1rem" not in page
        assert "margin-top:0.5rem" not in page
        assert ".card * + h3 { margin-top: var(--space-6); }" in self._partial("_forms.html")

    def test_a_heading_that_opens_a_form_is_not_pushed_off_it(self):
        """There the form's own padding is the gap, and adding to it would
        leave the heading floating above the box it introduces."""
        import re

        body = re.sub(r"<script>.*?</script>", "", self._rendered(), flags=re.S)
        for form, heading in (("add-model-form", "Adding a new model"),
                              ("add-professor-form", "Add a new professor")):
            at = body.index(f"<h3>{heading}</h3>")
            assert form in body[:at][-120:], heading

    def test_every_list_is_ruled_off_from_what_follows_it(self):
        """Without a line the two ran together and the heading read as part of
        the last row above it. Only the model list had one.

        Whatever follows the list carries the rule, not only a form: an
        endpoint is set up by editing a file, so what follows that list is the
        explanation of how rather than a form to fill in.
        """
        import re

        body = re.sub(r"<script>.*?</script>", "", self._rendered(), flags=re.S)
        body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
        for name in ("professors", "endpoints", "models"):
            at = body.index(f'id="{name}-list"')
            following = re.search(r"<(?:form|div)[^>]*>", body[at + 30:])
            assert following and "after-a-list" in following.group(0), name

    def test_each_of_those_holds_its_own_heading(self):
        """The heading takes its distance from the rule rather than adding to
        it, which is what keeps the three cards spaced alike."""
        import re

        body = re.sub(r"<script>.*?</script>", "", self._rendered(), flags=re.S)
        body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
        for name, heading in (("professors", "Add a new professor"),
                              ("endpoints", "Adding a new API endpoint"),
                              ("models", "Adding a new model")):
            at = body.index(f"<h3>{heading}</h3>")
            assert "after-a-list" in body[:at][-200:], name


class TestTheConversationFolderCard:
    """The settings page's own section for the two choices."""

    @pytest.fixture
    def page(self):
        return (WEBUI_SRC / "templates"
                / "settings.html").read_text()

    def card_words(self, page):
        """The card's wording as one line, so where the HTML wraps doesn't matter."""
        card = page.split('data-section="folder"')[1].split("</div>")[0]
        return " ".join(card.split())

    def test_both_boxes_are_on_the_page(self, page):
        assert 'id="keep-supplied-documents"' in page
        assert 'id="keep-job-outputs"' in page

    def test_every_section_of_the_page_is_placed_in_both_orders(self, page):
        """A section missing from an order list gets order 0 and jumps to the top."""
        app_module = sys.modules["_pu_webui_app"]
        on_page = set(re.findall(r'data-section="([a-z_]+)"', page))
        for order in (app_module._SETTINGS_ORDER_FIRST_RUN,
                      app_module._SETTINGS_ORDER_REPEAT):
            assert set(order) == on_page
            assert len(order) == len(set(order))

    def test_turning_the_outputs_box_off_says_the_file_is_not_thrown_away(self, page):
        """The words have to say what off means, because the name doesn't."""
        words = self.card_words(page)
        # The property, not the phrasing: turning it off must not read as
        # "and the file is gone".
        assert "download" in words

    def test_it_says_the_choice_is_not_retrospective(self, page):
        # Somewhere in the card, in whatever words: a change here does not
        # reach back into work already done.
        words = self.card_words(page)
        assert "existing conversations" in words or "already" in words

    def test_the_whole_row_is_the_target_and_not_just_the_box(self, page):
        """A 13px box is a poor target; the sentence beside it is a good one."""
        assert re.search(r"\.choice\s*\{[^}]*cursor:\s*pointer", page)
        card = page.split('data-section="folder"')[1].split("</div>")[0]
        assert card.count('<label class="choice">') == 2

    def test_a_failed_save_puts_the_box_back(self, page):
        """Otherwise the box says one thing and the file says another."""
        handler = page.split("async function saveFolderChoice")[1].split("\n}")[0]
        assert "box.checked = !box.checked" in handler

    def test_it_shows_what_the_file_says_rather_than_what_was_clicked(self, page):
        handler = page.split("async function saveFolderChoice")[1].split("\n}")[0]
        assert "renderFolderChoices(now)" in handler


class TestTheModelsSectionOnThePage:
    @pytest.fixture
    def page(self):
        return (WEBUI_SRC / "templates"
                / "settings.html").read_text()

    def test_untested_is_shown_as_its_own_state(self, page):
        """Three states, not two.

        'Cannot read images' is a fact about the model; 'not tested yet' is a
        job to do. Showing them identically is what made a capable model look
        broken with no hint that anything could be done about it.
        """
        assert "Not tested yet" in page

    def test_it_says_adding_a_model_spends_money(self, page):
        """Adding one makes real requests, billed to somebody's key.

        Small money, but somebody's — and the panel asks whose without saying
        why unless this is here. It sits after the list of name formats and
        before the fields, which is the last thing read before filling them in.
        """
        card = page.split('data-section="models"')[1].split('id="section-shared"')[0]
        words = " ".join(card.split())
        assert "tested before they are added" in words
        assert "less than a penny" in words
        assert "must be assigned to pay" in words
        assert "Test with whose key" in card

    def test_that_notice_comes_before_the_fields(self, page):
        """After it is filled in is too late to learn it costs anything."""
        card = page.split('data-section="models"')[1].split('id="section-shared"')[0]
        # Whitespace-normalised: the sentence wraps across lines in the source,
        # so looking for it as written finds nothing.
        card = " ".join(card.split())
        assert card.index("less than a penny") < card.index('class="inline-fields"')
        # And after the formats, so the reading order is: what to type, then
        # what typing it does.
        assert card.index("</ul>") < card.index("less than a penny")

    def test_it_shows_how_a_model_is_named(self, page):
        """'openai/gpt-5.2' is not guessable from an empty box."""
        assert "openai/gpt-5.2" in page


class TestTheSettingsPageIsInThreeTabs:
    """Seven cards in one column meant scrolling to find anything."""

    ASSIGNED = {
        "professors": "system", "shared": "system", "update": "system",
        "webui": "webui", "folder": "webui",
        "models": "models", "endpoints": "models",
    }

    @pytest.fixture
    def page(self):
        return (WEBUI_SRC / "templates"
                / "settings.html").read_text()

    def test_every_card_belongs_to_exactly_one_tab(self, page):
        """A card with no tab is a card nobody can ever reach."""
        cards = re.findall(r'data-section="([a-z_]+)"\s+data-tab="([a-z]+)"', page)
        assert dict(cards) == self.ASSIGNED
        assert len(cards) == len(re.findall(r'class="card"', page))

    def test_the_three_tabs_are_the_ones_asked_for(self, page):
        strip = page.split('id="tabs"')[1].split("</div>")[0]
        assert re.findall(r'data-tab="([a-z]+)"', strip) == ["system", "webui", "models"]

    def test_a_tab_is_not_dressed_as_a_button(self, page):
        """Buttons here are orange and mean "this does something"."""
        rule = page.split("#tabs button {")[1].split("}")[0]
        assert "background: none" in rule

    def test_the_chosen_tab_is_marked_by_more_than_colour(self, page):
        rule = page.split('#tabs button[aria-selected="true"] {')[1].split("}")[0]
        assert "border-bottom-color" in rule

    def test_the_strip_can_be_hidden_before_there_is_anything_in_it(self, page):
        """#tabs sets display, which beats the browser's own [hidden]."""
        assert "#tabs[hidden] { display: none; }" in page

    def test_the_arrow_keys_move_between_tabs(self, page):
        handler = page.split('document.getElementById("tabs").addEventListener("keydown"')[1]
        handler = handler.split("\n});")[0]
        for key in ("ArrowLeft", "ArrowRight", "Home", "End"):
            assert key in handler

    def test_only_the_chosen_tab_is_a_tab_stop(self, page):
        """Otherwise Tab walks through three tabs before reaching a setting."""
        fn = page.split("function showTab")[1].split("\n}")[0]
        assert 'setAttribute("tabindex", "-1")' in fn
        assert 'removeAttribute("tabindex")' in fn

    def test_the_panel_says_which_tab_it_belongs_to(self, page):
        assert 'role="tabpanel"' in page
        fn = page.split("function showTab")[1].split("\n}")[0]
        assert "aria-labelledby" in fn

    def test_the_order_the_server_sends_still_applies(self, page):
        """Tabs decide what is shown; the server still decides the order."""
        fn = page.split("function showTab")[1].split("\n}")[0]
        assert "style.order" not in fn, "showTab must not take over ordering"
        assert "function applyOrder" in page

    def test_every_section_is_still_placed_in_both_orders(self, page):
        app_module = sys.modules["_pu_webui_app"]
        on_page = set(re.findall(r'data-section="([a-z_]+)"', page))
        for order in (app_module._SETTINGS_ORDER_FIRST_RUN,
                      app_module._SETTINGS_ORDER_REPEAT):
            assert set(order) == on_page


class TestTheModelsPanelReadsAsOnePage:
    """Five things that made the panel look assembled rather than designed."""

    @pytest.fixture
    def page(self):
        return _rendered_template("settings.html")

    def test_a_list_is_set_like_the_sentence_above_it(self, page):
        rule = page.split(".card ul, .card ol {")[1].split("}")[0]
        assert "font-size: var(--text-sm)" in rule
        assert "color: var(--text-muted)" in rule

    def test_no_list_is_trapped_inside_a_paragraph(self, page):
        """A browser closes a <p> the moment a block starts inside it.

        The list then sits outside .hint and inherits none of it — which is why
        it looked nothing like the text introducing it, however the list itself
        was styled.
        """
        assert not re.search(r'<p class="hint[^"]*">(?:(?!</p>).)*?<(ul|ol|div)\b',
                             page, re.S)

    def test_what_a_model_can_do_is_not_coloured_as_good_news(self, page):
        """Green means "this is set". Being able to read images is neither
        good news nor bad — it is a fact about the model."""
        assert 'badge ${m.supports_vision ? "can"' in page
        assert ".badge.can {" in page

    def test_that_badge_is_the_sandbox_orange_and_not_a_new_colour(self, page):
        """Same hue as #F58025, lightened — one family, not a second accent."""
        import colorsys
        for token in ("--badge-can-bg", "--badge-can-text"):
            found = re.search(rf"{token}:\s*#([0-9a-fA-F]{{6}})", page)
            r, g, b = (int(found.group(1)[i:i+2], 16) / 255 for i in (0, 2, 4))
            hue = colorsys.rgb_to_hls(r, g, b)[0] * 360
            assert 18 <= hue <= 36, f"{token} is hue {hue:.0f}°, not the orange's"

    def test_there_is_a_line_before_adding_one(self, page):
        """Without it the heading read as part of the last row above it."""
        rule = page.split(".after-a-list {")[1].split("}")[0]
        assert "border-top" in rule
        assert 'id="add-model-form" class="after-a-list"' in page

    def test_links_are_not_the_browsers_own_blue(self, page):
        """There was no rule at all, so they came out louder than anything
        else on a page made of one orange and greys."""
        assert "a { color: var(--link); }" in page
        assert re.search(r"--link:\s*#[0-9a-fA-F]{6}", page)

    def test_the_link_colour_complements_the_orange(self, page):
        """Its complement, not another warm colour fighting it."""
        import colorsys
        found = re.search(r"--link:\s*#([0-9a-fA-F]{6})", page)
        r, g, b = (int(found.group(1)[i:i+2], 16) / 255 for i in (0, 2, 4))
        hue = colorsys.rgb_to_hls(r, g, b)[0] * 360
        assert 190 <= hue <= 220, f"hue {hue:.0f}° is not opposite the orange's 26°"

    def test_the_button_is_not_hard_against_the_fields(self, page):
        assert "button.after-fields { margin-top:" in page
        assert 'id="add-model-btn" class="after-fields"' in page

    def test_every_new_colour_can_be_read_in_both_themes(self, page):
        """The floor for text is 4.5:1, and a colour chosen by eye misses it."""
        def lum(h):
            h = h.lstrip("#")
            parts = [int(h[i:i+2], 16) / 255 for i in (0, 2, 4)]
            f = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in parts]
            return 0.2126 * f[0] + 0.7152 * f[1] + 0.0722 * f[2]

        def ratio(a, b):
            hi, lo = sorted((lum(a), lum(b)), reverse=True)
            return (hi + 0.05) / (lo + 0.05)

        def token(where, name):
            return re.findall(rf"{name}:\s*(#[0-9a-fA-F]{{6}})", where)[-1]

        light, dark = page.split('[data-theme="dark"]')
        for half, theme in ((light, "light"), (dark, "dark")):
            assert ratio(token(half, "--badge-can-bg"),
                         token(half, "--badge-can-text")) >= 4.5, theme
            assert ratio(token(half, "--panel-bg"), token(half, "--link")) >= 4.5, theme
            assert ratio(token(half, "--bg"), token(half, "--link")) >= 4.5, theme


class TestTheTwoStorageBoxesDoNotOverlap:
    """One is what goes in, the other is what comes out.

    The labels said otherwise: the outputs box claimed to cover "the files
    supplied for a job" as well, which made the pair read as one box and one
    box-plus-a-bit. The code never worked that way — keep_supplied_documents is
    consulted for chat attachments and for files handed to a plugin, and
    keep_job_outputs only for where a finished job writes its result.
    """

    @pytest.fixture
    def boxes(self):
        page = _rendered_template("settings.html")
        card = page.split('data-section="folder"')[1].split('id="section-shared"')[0]
        found = re.findall(
            r'<span class="name">(.*?)</span>.*?<span class="sub">(.*?)</span>',
            card, re.S)
        return [(" ".join(n.split()), " ".join(s.split())) for n, s in found]

    def test_there_are_two_of_them(self, boxes):
        assert len(boxes) == 2

    def test_the_first_is_about_what_goes_in(self, boxes):
        name, sub = boxes[0]
        assert "supply" in name
        # Both ways a file arrives, since the setting covers both.
        assert "chat" in sub and "plugin" in sub

    def test_the_second_is_about_what_comes_out_and_only_that(self, boxes):
        """The claim that made them look redundant."""
        name, sub = boxes[1]
        assert "produces" in name
        assert "generates" in sub or "produced" in sub
        assert "supplied" not in sub, "the outputs box does not decide about inputs"

    def test_what_the_code_actually_does_matches_that_split(self):
        """Read from the source, so the labels cannot drift from the behaviour.

        If a future change makes the outputs setting cover inputs too, this
        fails and the labels get revisited rather than quietly becoming wrong
        again.
        """
        app = (WEBUI_SRC / "app.py").read_text()
        jobs = (WEBUI_SRC / "jobs.py").read_text()
        # What goes in: consulted on both routes a file can arrive by.
        assert app.count("_keep_supplied_document(professor") == 2
        # What comes out: one place, and it is where a job writes its result.
        assert jobs.count('is_on("keep_job_outputs"') == 1
        assert 'is_on("keep_supplied_documents"' not in jobs


class TestHowSomebodyGetsAKey:
    """Asked in two places — first-run setup and the settings page — and the
    answer is the same in both."""

    def _settings(self):

        return (WEBUI_SRC / "templates"
                / "settings.html").read_text()

    def _setup(self):

        return (WEBUI_SRC / "setup_web.py").read_text()

    def test_both_send_people_to_the_request_form(self):
        for source in (self._settings(), self._setup()):
            assert "ServiceNow request" in source
            assert "sc_cat_item" in source

    def test_both_say_who_can_submit_one(self):
        """A professor, for themselves or somebody they supervise — the form
        turns anybody else away, which is worth knowing beforehand."""
        for source in (self._settings(), self._setup()):
            assert "supervise" in source
            assert "Non-faculty personnel" in source

    def test_the_link_opens_in_its_own_tab(self):
        source = self._settings()
        at = source.index("sc_cat_item")
        assert 'target="_blank"' in source[at:at + 400]
        assert 'rel="noopener"' in source[at:at + 400]

    def test_the_product_is_called_one_thing(self):
        """Five pages, one name."""
        import re

        templates = WEBUI_SRC / "templates"
        for page in sorted(templates.glob("*.html")):
            text = page.read_text()
            assert "PU AI Sandbox" not in text, page.name
            assert not re.search(r"Princeton (?!University)\w* ?AI Sandbox", text), page.name


class TestTheUpdatesCard:
    """Drawn on the settings page, and pointed at from the chat page."""

    def _settings(self):
        return _rendered_template("settings.html")

    def test_it_is_on_the_system_tab(self):
        page = self._settings()
        assert 'data-section="update" data-tab="system"' in page

    def test_it_is_placed_in_both_orders(self):
        """A card the server never names keeps the default order of nought and
        floats to the top of the tab, above everything else."""
        app_module = sys.modules["_pu_webui_app"]
        assert "update" in app_module._SETTINGS_ORDER_FIRST_RUN
        assert "update" in app_module._SETTINGS_ORDER_REPEAT

    def test_it_comes_last_on_a_first_run_and_near_the_front_after(self):
        """A copy downloaded minutes ago has nothing to update."""
        app_module = sys.modules["_pu_webui_app"]
        assert app_module._SETTINGS_ORDER_FIRST_RUN[-1] == "update"
        assert app_module._SETTINGS_ORDER_REPEAT[0] == "update"

    def test_what_has_changed_goes_in_as_text(self):
        """Those summaries are written in another repository and arrive over
        the network. innerHTML would be running whatever they contain."""
        page = self._settings()
        block = page[page.index("function drawWhatWasFound"):]
        block = _code_only(block[:block.index("async function askAboutUpdates")])
        assert "textContent" in block
        assert "innerHTML" not in block

    def test_it_says_that_settings_and_work_are_not_touched(self):
        """The first question anybody sensible asks before pressing it."""
        page = self._settings()
        card = page[page.index('id="section-update"'):]
        card = card[:card.index('id="section-endpoints"')]
        assert "<code>settings</code>" in card and "<code>data</code>" in card

    def test_it_warns_that_everybody_is_signed_out(self):
        page = self._settings()
        card = page[page.index('id="section-update"'):]
        card = card[:card.index('id="section-endpoints"')]
        assert "signs everybody out" in card
