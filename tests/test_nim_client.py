from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import nim_client


def _fake_chunk(content):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=content))])


def test_chat_completion_retries_after_mid_stream_failure():
    """Regression test for a real triggered run: a stream got a clean 200 OK,
    ran for 10 minutes, then NVIDIA's server returned an error mid-stream.
    The client's own max_retries doesn't cover this (it only retries a
    failure on the initial request, before streaming begins), so
    chat_completion needs its own outer retry around the whole call."""
    good_stream = [_fake_chunk("LINE: x"), _fake_chunk("\n")]
    call_count = {"n": 0}

    def fake_create(**kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("Internal server error")
        return iter(good_stream)

    fake_client = MagicMock()
    fake_client.chat.completions.create.side_effect = fake_create

    with patch("nim_client.get_client", return_value=fake_client):
        result = nim_client.chat_completion("prompt")

    assert result == "LINE: x\n"
    assert call_count["n"] == 2


def test_chat_completion_raises_after_exhausting_all_stream_retries():
    fake_client = MagicMock()
    fake_client.chat.completions.create.side_effect = RuntimeError("Internal server error")

    with patch("nim_client.get_client", return_value=fake_client):
        with pytest.raises(RuntimeError, match="Internal server error"):
            nim_client.chat_completion("prompt")

    assert fake_client.chat.completions.create.call_count == nim_client._MAX_STREAM_RETRIES


def test_chat_completion_ignores_chunks_with_no_content():
    """A thinking-mode stream sends chunks whose reasoning tokens land in a
    field other than `content` — those must not become empty/None entries
    in the final joined string."""
    stream = [
        _fake_chunk(None),
        _fake_chunk("real "),
        SimpleNamespace(choices=[]),
        _fake_chunk("answer"),
    ]
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = iter(stream)

    with patch("nim_client.get_client", return_value=fake_client):
        result = nim_client.chat_completion("prompt")

    assert result == "real answer"
