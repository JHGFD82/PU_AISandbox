"""Error types shared across the CLI and all plugins."""


class CLIError(Exception):
    """Signals a problem that should be reported to the user and stop the current command.

    Raising ``CLIError`` is the standard way for any part of the application to
    say "something went wrong that the user needs to fix." The main entry point
    catches it, prints the message to the terminal, and exits with a non-zero
    status code so that scripts can detect the failure.

    Use this instead of a bare ``Exception`` whenever the error message is meant
    for the person running the command rather than for a developer reading a
    traceback.
    """
