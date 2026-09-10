"""The NAT adapter is a thin shell over watchdoc.pipeline; these tests only
check that its config defaults come from the core package rather than being
retyped here."""
import pytest

pytest.importorskip("nat", reason="watchdoc_detector requires nvidia-nat")

from watchdoc import nim_client  # noqa: E402
from watchdoc.claims import DEFAULT_ENSEMBLE_SIZE  # noqa: E402
from watchdoc_detector.watchdoc_detector import WatchdocDetectorFunctionConfig  # noqa: E402


def test_config_defaults_mirror_the_core_defaults():
    config = WatchdocDetectorFunctionConfig()

    assert config.model == nim_client.DEFAULT_SETTINGS.model
    assert config.temperature == nim_client.DEFAULT_SETTINGS.temperature
    assert config.nim_timeout_seconds == nim_client.DEFAULT_SETTINGS.timeout_seconds
    assert config.max_concurrent_nim_calls == nim_client.DEFAULT_SETTINGS.max_concurrent_requests
    assert config.ensemble_size == DEFAULT_ENSEMBLE_SIZE
    assert config.commit_fixes is True
    assert config.repo_root is None
