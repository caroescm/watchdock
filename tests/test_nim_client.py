import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import openai
import pytest

from watchdoc import nim_client


@pytest.fixture(autouse=True)
def no_backoff_wait():
    """Backoff between attempts is real in production; tests record the
    delays instead of sleeping through them."""
    with patch.object(nim_client, "_sleep") as sleep:
        yield sleep


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

    with patch("watchdoc.nim_client.get_client", return_value=fake_client):
        result = nim_client.chat_completion("prompt")

    assert result == "LINE: x\n"
    assert call_count["n"] == 2


def test_chat_completion_raises_after_exhausting_all_stream_retries(no_backoff_wait):
    fake_client = MagicMock()
    fake_client.chat.completions.create.side_effect = RuntimeError("Internal server error")

    with patch("watchdoc.nim_client.get_client", return_value=fake_client):
        with pytest.raises(RuntimeError, match="Internal server error"):
            nim_client.chat_completion("prompt")

    assert fake_client.chat.completions.create.call_count == nim_client._MAX_STREAM_RETRIES
    # one wait between each pair of attempts, none after the last failure
    assert no_backoff_wait.call_count == nim_client._MAX_STREAM_RETRIES - 1


def _api_error(cls, status):
    response = httpx.Response(status, request=httpx.Request("POST", "https://nim.test/v1"))
    return cls("nope", response=response, body=None)


def test_chat_completion_does_not_retry_a_bad_api_key(no_backoff_wait):
    """A 401 is the request being wrong, not the server being flaky; retrying
    only delays the failure and the message the operator needs to see."""
    fake_client = MagicMock()
    fake_client.chat.completions.create.side_effect = _api_error(openai.AuthenticationError, 401)

    with patch("watchdoc.nim_client.get_client", return_value=fake_client):
        with pytest.raises(openai.AuthenticationError):
            nim_client.chat_completion("prompt")

    assert fake_client.chat.completions.create.call_count == 1
    no_backoff_wait.assert_not_called()


def test_chat_completion_retries_rate_limits_and_server_errors():
    fake_client = MagicMock()
    fake_client.chat.completions.create.side_effect = [
        _api_error(openai.RateLimitError, 429),
        iter([_fake_chunk("ok")]),
    ]
    with patch("watchdoc.nim_client.get_client", return_value=fake_client), \
         patch.object(nim_client, "_MAX_STREAM_RETRIES", 3):
        assert nim_client.chat_completion("prompt") == "ok"

    assert nim_client._is_retryable(_api_error(openai.InternalServerError, 500))
    assert nim_client._is_retryable(openai.APIConnectionError(request=httpx.Request("POST", "https://nim.test")))
    assert not nim_client._is_retryable(_api_error(openai.BadRequestError, 400))


def test_backoff_grows_exponentially_with_jitter_and_is_capped():
    with patch.object(nim_client.random, "uniform", return_value=1.0):
        assert nim_client._backoff_seconds(1) == 2.0
        assert nim_client._backoff_seconds(2) == 4.0
        assert nim_client._backoff_seconds(3) == 8.0
        assert nim_client._backoff_seconds(10) == nim_client._BACKOFF_MAX_SECONDS
    assert 1.0 <= nim_client._backoff_seconds(1) <= 3.0


def test_retry_waits_outside_the_concurrency_slot():
    """While a call backs off it must not hold a stream slot: another caller
    should be able to acquire the gate during the wait."""
    slot_free_during_wait = {}

    def fake_sleep(_):
        slot_free_during_wait["free"] = nim_client._request_slots.acquire(blocking=False)
        if slot_free_during_wait["free"]:
            nim_client._request_slots.release()

    fake_client = MagicMock()
    fake_client.chat.completions.create.side_effect = [RuntimeError("boom"), iter([_fake_chunk("ok")])]

    with patch("watchdoc.nim_client.get_client", return_value=fake_client), \
         patch.object(nim_client, "_sleep", fake_sleep), \
         patch.object(nim_client, "_request_slots", threading.BoundedSemaphore(1)):
        assert nim_client.chat_completion("prompt") == "ok"

    assert slot_free_during_wait["free"] is True


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

    with patch("watchdoc.nim_client.get_client", return_value=fake_client):
        result = nim_client.chat_completion("prompt")

    assert result == "real answer"


def test_chat_completion_caps_concurrent_requests():
    """Per-claim fan-out can request (claims x ensemble x targets) calls at
    once; the semaphore must bound how many streams are actually open
    simultaneously while still letting every call complete."""
    counter_lock = threading.Lock()
    active = {"now": 0, "max": 0}
    # Each in-flight pair must rendezvous here before either finishes. With
    # the cap at 2 that is exactly the two calls holding slots, so the test
    # observes true concurrency without depending on sleep timing. If the
    # cap leaked a third call in, `max` would record it.
    pair = threading.Barrier(2, timeout=5)

    def fake_create(**kwargs):
        with counter_lock:
            active["now"] += 1
            active["max"] = max(active["max"], active["now"])
        pair.wait()
        with counter_lock:
            active["now"] -= 1
        return iter([_fake_chunk("ok")])

    fake_client = MagicMock()
    fake_client.chat.completions.create.side_effect = fake_create
    results = []

    with patch("watchdoc.nim_client.get_client", return_value=fake_client), \
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
    assert active["max"] == 2  # the cap was reached (calls did overlap) and never exceeded


def test_get_client_is_built_once_and_reused(monkeypatch):
    """Connection reuse across the fan-out depends on one client per process,
    not one per call."""
    monkeypatch.setenv("NVIDIA_API_KEY", "k")
    monkeypatch.setattr(nim_client, "_client", None)

    assert nim_client.get_client() is nim_client.get_client()


def test_configure_applies_model_temperature_and_concurrency():
    saved = dict(nim_client._settings)
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = iter([_fake_chunk("ok")])
    try:
        nim_client.configure(model="some/other-model", temperature=0.7, max_concurrent_requests=3)
        with patch("watchdoc.nim_client.get_client", return_value=fake_client):
            nim_client.chat_completion("p")
        assert nim_client._request_slots._initial_value == 3
    finally:
        nim_client.configure(**saved)  # restores the 8-slot gate for the other tests

    kwargs = fake_client.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "some/other-model"
    assert kwargs["temperature"] == 0.7


def test_a_zero_retry_setting_still_makes_one_attempt():
    """Guards the old `raise last_error` -> `raise None` path: with the retry
    constant at 0 the call must run once and surface its real error."""
    fake_client = MagicMock()
    fake_client.chat.completions.create.side_effect = RuntimeError("real error")

    with patch("watchdoc.nim_client.get_client", return_value=fake_client), \
         patch.object(nim_client, "_MAX_STREAM_RETRIES", 0):
        with pytest.raises(RuntimeError, match="real error"):
            nim_client.chat_completion("p")

    assert fake_client.chat.completions.create.call_count == 1
