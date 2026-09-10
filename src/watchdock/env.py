"""Every environment variable Watchdock reads, and the one place that reads them.

Required:
    GITHUB_TOKEN        API token for reading the PR and posting results.
    GITHUB_REPOSITORY   ``owner/repo`` of the PR being checked.
    NVIDIA_API_KEY      NIM API key for the model calls.

Optional:
    GITHUB_EVENT_PATH   The Action's event payload; the PR number comes from
                        here when the run input doesn't name one.
    GITHUB_WORKSPACE    The calling repository's checkout; the default
                        ``repo_root`` when the workflow config leaves it unset.

Everything else an operator can tune is a field on the NAT workflow config
(see ``watchdock_detector/configs/config.yml``), not an environment variable.
"""
import os

from watchdock.errors import ConfigError


def require(name: str, hint: str) -> str:
    """The value of a required variable, or a ConfigError that says how to set it."""
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"{name} is not set. {hint}")
    return value


def optional(name: str) -> str | None:
    """The value of an optional variable; an empty value counts as unset."""
    return os.environ.get(name) or None
