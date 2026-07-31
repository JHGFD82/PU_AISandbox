

class TestTheFormShowsWhatABlankBoxMeans:
    """A blank sampling box in the web form runs on this plugin's settings.

    Those settings are the plugin's own file, then the group's shared file,
    then this person's preferences — so someone who sets a temperature of their
    own should see that number, not a description of where it comes from.
    """

    def test_the_action_reports_the_values_it_would_use(self):
        from plugins.translation.plugin import ui_action
        from src.settings import (
            TRANSLATION_MAX_TOKENS,
            TRANSLATION_TEMPERATURE,
            TRANSLATION_TOP_P,
        )

        assert ui_action.sampling == {
            "temperature": TRANSLATION_TEMPERATURE,
            "top_p": TRANSLATION_TOP_P,
            "max_tokens": TRANSLATION_MAX_TOKENS,
        }

    def test_those_values_are_the_ones_a_blank_box_actually_runs_on(self):
        """The same constants the service hands to _resolve_sampling_params.

        Read from the file rather than imported: the service is reachable only
        once the plugin has registered it into sys.modules, and this is a
        question about what the code says, not about running it.
        """
        from pathlib import Path

        service = (Path(__file__).resolve().parents[1]
                   / "src" / "services" / "translation_service.py").read_text()
        assert "TRANSLATION_TEMPERATURE, TRANSLATION_TOP_P, TRANSLATION_MAX_TOKENS" in service, (
            "the form would be reporting settings the service does not use"
        )
