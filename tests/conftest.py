"""Suite-wide fixtures. The GitHub fakes themselves live in tests/fakes.py so
tests import them as a normal module instead of importing conftest."""
import pytest

from watchdoc import nim_client


@pytest.fixture(autouse=True)
def isolated_default_nim_client(monkeypatch):
    """No test may leak a configure() call into the next: every test starts
    from the default client and any configure() it makes is undone."""
    monkeypatch.setattr(nim_client, "_default", nim_client.NimClient())
