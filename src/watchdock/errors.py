"""The exceptions Watchdock raises on purpose.

Anything else that escapes is a bug or an unexpected third-party failure.
"""


class WatchdockError(Exception):
    """Base class for every deliberate Watchdock failure."""


class ConfigError(WatchdockError):
    """Missing or malformed configuration: an environment variable, the
    ``.watchdock.yml`` file, or the run input."""


class PipelineError(WatchdockError):
    """The run completed and reported, but at least one target could not be
    read, checked or delivered. Raised last so the Action goes red without
    hiding what was checked."""
