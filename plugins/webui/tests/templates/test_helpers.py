"""Tests for plugins/webui/src/templates/_helpers.html: the script functions every page shares."""

from __future__ import annotations

from plugins.webui.tests.helpers import (
    WEBUI_SRC,
    _code_only,
    _rendered_chat,
)


class TestWaitingForARestartIsWrittenOnce:
    """It used to live in chat.html and name that page's own status line, so
    the settings page could not have called it without getting a null."""

    def test_it_is_in_the_shared_partial(self):
        helpers = (WEBUI_SRC / "templates"
                   / "_helpers.html").read_text()
        assert "function waitForTheSandboxToComeBack(say)" in helpers
        assert "install-busy" not in _code_only(helpers), (
            "it still names one page's element")

    def test_the_page_is_only_declared_once_how_long_to_wait(self):
        """Two `const RESTART_PATIENCE_MS` in one scope is a SyntaxError that
        would take the whole of chat.html's script with it."""
        assert _rendered_chat().count("const RESTART_PATIENCE_MS") == 1

    def test_it_reloads_the_whole_window_and_not_just_the_panel(self):
        """The settings page is also shown inside the chat page's settings
        panel. Reloading that frame alone would leave a fresh settings page
        inside a chat page still talking to a server that no longer exists."""
        helpers = (WEBUI_SRC / "templates"
                   / "_helpers.html").read_text()
        assert "window.top.location.reload()" in helpers


class TestNoPageCallsAHelperItHasNot:
    """These pages share behaviour by including partials, and a helper written
    into one page directly is a helper the next page will call and not have.
    That is not a theory: readableError lived in settings.html, chat.html
    called it, and every failed plugin install threw a ReferenceError inside
    its own error handler — so the dialog said nothing at all and the only
    sign was a bare 400 in the network tab."""

    PAGES = ("chat.html", "settings.html", "shared_settings.html", "unlock.html")

    def _script(self, name):
        import re

        from fastapi.templating import Jinja2Templates

        directory = WEBUI_SRC / "templates"
        page = Jinja2Templates(directory=str(directory)).get_template(name).render(
            request=None)
        return "\n".join(re.findall(r"<script>(.*?)</script>", page, re.S))

    def _defined_and_called(self, name):
        import re

        js = re.sub(r"//[^\n]*", "", self._script(name))
        defined = set(re.findall(r"function\s+([A-Za-z_$][\w$]*)", js))
        # Bare calls only: anything after a dot is a method on something else,
        # and whether that exists is not a question this can answer.
        called = set(re.findall(r"(?<![.\w$])([a-z_$][A-Za-z0-9_$]*)\s*\(", js))
        return defined, called

    def test_every_helper_a_page_calls_is_a_helper_it_has(self):
        """Only names some page defines are considered, which is what keeps
        this quiet: a browser built-in, a local variable or a word inside a
        string is never a project helper and is never flagged.

        Deliberately no attempt to strip string or regex literals first.
        chat.html's markdown parser holds backticks inside regex literals, and
        every regex-based way of removing literals swallowed real code along
        with them — including the function definitions this needs to see.
        """
        everything = {}
        for page in self.PAGES:
            everything[page] = self._defined_and_called(page)
        helpers = set().union(*(defined for defined, _ in everything.values()))

        missing = {}
        for page, (defined, called) in everything.items():
            absent = sorted((called & helpers) - defined)
            if absent:
                missing[page] = absent
        assert not missing, (
            "called on a page that does not define it, and defined on another:\n"
            + "\n".join(f"  {page}: {', '.join(names)}" for page, names in missing.items()))

    def test_the_shared_helper_is_shared_rather_than_copied(self):
        """One definition reaching four pages, not four definitions."""
        import re

        directory = WEBUI_SRC / "templates"
        partial = (directory / "_helpers.html").read_text()
        assert "function readableError" in partial
        for page in self.PAGES:
            source = (directory / page).read_text()
            assert '{% include "_helpers.html" %}' in source, page
            assert "function readableError" not in source, f"{page} has its own copy"
            # And it arrives exactly once in what the browser receives.
            assert len(re.findall(r"function\s+readableError",
                                  self._script(page))) == 1, page
