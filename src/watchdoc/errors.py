"""The exceptions Watchdoc raises on purpose.

Anything else that escapes is a bug or an unexpected third-party failure.
"""


class WatchdocError(Exception):
    """Base class for every deliberate Watchdoc failure."""


class ConfigError(WatchdocError):
    """Missing or malformed configuration: an environment variable, the
    ``.watchdoc.yml`` file, or the run input."""


class PipelineError(WatchdocError):
    """The run completed and reported, but at least one target could not be
    read, checked or delivered. Raised last so the Action goes red without
    hiding what was checked."""
