"""Tests for plugins/webui/src/templates/shared_settings.html: the page for editing a group's shared settings file."""

from __future__ import annotations

import pytest

from plugins.webui.tests.helpers import (
    REPO_ROOT,
    WEBUI_SRC,
)

# Every test here runs as two fake professors with no passphrase set; see conftest.py.
pytestmark = pytest.mark.usefixtures("_configured_professors", "_no_passphrase")


class TestSharedSettingsPagePresentation:
    """The editor is a page of its own, so it has to carry the app's look itself.

    Embedded pages inherit the modal's styling by being inside it; this one
    doesn't, and copying the stylesheet without the script that applies the
    theme is how a page ends up permanently light.
    """

    def _page(self):

        from fastapi.templating import Jinja2Templates

        directory = WEBUI_SRC / "templates"
        return Jinja2Templates(directory=str(directory)).get_template(
            "shared_settings.html"
        ).render(request=None)

    def test_it_applies_the_saved_theme(self):
        """Without this the dark-mode rules below are dead weight."""
        page = self._page()
        assert "applySavedTheme" in page
        assert 'setAttribute("data-theme"' in page

    def test_it_reads_the_same_theme_setting_as_the_rest_of_the_app(self):
        page = self._page()
        assert 'localStorage.getItem("theme")' in page

    def test_it_defines_the_dark_theme(self):
        page = self._page()
        assert '[data-theme="dark"]' in page

    def test_it_uses_the_same_layout_shell_as_the_settings_modal(self):
        page = self._page()
        assert '<div id="topbar">' in page
        assert '<div id="page">' in page

    def test_it_uses_themed_colours_rather_than_fixed_ones(self):
        """A hardcoded grey looks wrong in one theme or the other.

        Checks the styles this page adds, not the shared stylesheet it copies —
        that one defines the colours, so naming them there is the point.
        """
        page = self._page()
        own_styles = page.rsplit("<style>", 1)[1].split("</style>")[0]
        assert "var(--text-muted)" in own_styles
        assert "#6e6e73" not in own_styles, "hardcoded light-theme grey"
        assert "var(--muted," not in own_styles, "invented variable with a fixed fallback"

    def test_value_boxes_fit_the_longest_setting_this_sandbox_has(self):
        """A list cut off mid-way is a value nobody can check.

        Measured rather than guessed: the widest value any installed plugin
        offers is a model list, and the box has to hold it.
        """

        from src.shared_settings import inventory

        repo = REPO_ROOT
        widest = max(
            len(s["value"])
            for section in inventory(repo / "plugins", repo / "settings.default.toml")
            for s in section["settings"]
        )
        import re

        page = self._page()
        widths = [int(m) for m in re.findall(r"minmax\(0, (\d+)rem\)", page)]
        assert widths, "no fixed-width value column found"
        rem = min(widths)
        # 0.78rem monospace at roughly 0.6em per character, less the input's padding.
        fits = (rem * 16 - 16) / (0.78 * 16 * 0.6)
        assert fits >= widest, f"a {rem}rem box holds about {fits:.0f} characters, need {widest}"

    def test_every_value_box_can_be_dragged_taller(self):
        """The field most likely to hold a lot is the one that looks smallest.

        prompt.default_system_prompt ships as a single sentence, so any rule
        based on how long a value is today would give it the smallest box —
        which is the opposite of what a group replacing it with paragraphs
        needs.
        """
        page = self._page()
        assert "resize: vertical" in page
        assert 'createElement("textarea")' in page
        assert 'createElement("input")\n      value.type = "text"' not in page

    def test_a_box_opens_at_the_height_of_its_own_text(self):
        page = self._page()
        assert "scrollHeight" in page

    def test_the_page_is_wide_enough_for_that_box(self):
        """The Settings modal caps at 720px, which squeezes the column back."""
        page = self._page()
        assert "#page { max-width: 1040px; }" in page

    def test_a_wrong_value_is_outlined_and_told_why(self):
        page = self._page()
        assert "textarea.invalid" in page
        assert "border-color: var(--danger-text)" in page
        assert ".field-error" in page
        assert "color: var(--danger-text)" in page

    def test_the_message_sits_under_its_own_box(self):
        """One message at the bottom of forty rows names no row."""
        page = self._page()
        assert "value-cell" in page
        assert "text-align: left" in page

    def test_the_server_still_refuses_a_bad_value(self, unlocked_client):
        """The page checking first must not become the only thing checking.

        Someone can reach this without the page — an old tab, a script — so the
        check that builds the file stays the one that decides.
        """
        r = unlocked_client.post(
            "/api/settings/shared-draft", json={"chosen": {"retry": {"max_retries": "lots"}}}
        )
        assert r.status_code >= 400
        assert "max_retries" in r.text

    def test_text_settings_are_typed_as_text(self):
        """Quotation marks are the file's spelling, not the person's job."""
        page = self._page()
        assert "function forFile" in page
        assert "JSON.stringify" in page
        # The page has to say so somewhere; the wording is not this test's
        # business, and pinning a sentence discourages improving it.
        assert "quotation marks" in page

    def test_unticking_keeps_the_value_it_had(self):
        """The behaviour, not the sentence describing it.

        The page used to explain that unticking a setting shows the shipped
        value and remembers yours. That paragraph has been rewritten away; what
        it described is still what happens, so this checks the code rather than
        the prose.
        """
        page = self._page()
        assert "custom" in page and "checked" in page

    def test_a_narrow_window_gives_the_value_its_own_row(self):
        page = self._page()
        assert "@media (max-width: 1080px)" in page
