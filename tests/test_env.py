import pytest

from watchdock import env
from watchdock.errors import ConfigError


def test_require_returns_the_value(monkeypatch):
    monkeypatch.setenv("WATCHDOCK_TEST_VAR", "x")
    assert env.require("WATCHDOCK_TEST_VAR", "hint") == "x"


def test_require_treats_missing_and_empty_the_same_and_includes_the_hint(monkeypatch):
    monkeypatch.setenv("WATCHDOCK_TEST_VAR", "")
    with pytest.raises(ConfigError, match="WATCHDOCK_TEST_VAR is not set. set it like so"):
        env.require("WATCHDOCK_TEST_VAR", "set it like so")


def test_optional_maps_empty_to_none(monkeypatch):
    monkeypatch.setenv("WATCHDOCK_TEST_VAR", "")
    assert env.optional("WATCHDOCK_TEST_VAR") is None
    monkeypatch.setenv("WATCHDOCK_TEST_VAR", "v")
    assert env.optional("WATCHDOCK_TEST_VAR") == "v"
