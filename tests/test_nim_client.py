import threading
import time
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


def test_chat_completion_caps_concurrent_requests():
    """Per-claim fan-out can request (claims x ensemble x targets) calls at
    once; the semaphore must bound how many streams are actually open
    simultaneously while still letting every call complete."""
    counter_lock = threading.Lock()
    active = {"now": 0, "max": 0}

    def fake_create(**kwargs):
        with counter_lock:
            active["now"] += 1
            active["max"] = max(active["max"], active["now"])
        time.sleep(0.02)
        with counter_lock:
            active["now"] -= 1
        return iter([_fake_chunk("ok")])

    fake_client = MagicMock()
    fake_client.chat.completions.create.side_effect = fake_create
    results = []

    with patch("nim_client.get_client", return_value=fake_client), \
         patch.object(nim_client, "_request_slots", threading.BoundedSemaphore(2)):
        threads = [
            threading.Thread(target=lambda: results.append(nim_client.chat_completion("p")))
            for _ in range(6)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    assert results == ["ok"] * 6
    assert active["max"] <= 2
