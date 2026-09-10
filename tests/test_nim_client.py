import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import openai
import pytest

from watchdock import nim_client
from watchdock.errors import ConfigError
from watchdock.nim_client import NimClient, Settings, backoff_seconds, is_retryable


def _chunk(content):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=content))])


def _api_error(cls, status):
    response = httpx.Response(status, request=httpx.Request("POST", "https://nim.test/v1"))
    return cls("nope", response=response, body=None)


def _client(*, side_effect=None, return_value=None, sleep=None, **settings):
    """A NimClient over a fake OpenAI client, with no real waiting between attempts."""
    fake_openai = MagicMock()
    if side_effect is not None:
        fake_openai.chat.completions.create.side_effect = side_effect
    else:
        fake_openai.chat.completions.create.return_value = return_value
    sleep = sleep or MagicMock()
    return NimClient(Settings(**settings), openai_client=fake_openai, sleep=sleep), fake_openai, sleep


def test_chat_completion_joins_content_deltas_and_ignores_chunks_without_content():
    """A thinking-mode stream sends chunks whose reasoning tokens land in a
    field other than `content`; those must not become entries in the answer."""
    stream = [_chunk(None), _chunk("real "), SimpleNamespace(choices=[]), _chunk("answer")]
    client, _, _ = _client(return_value=iter(stream))

    assert client.chat_completion("prompt") == "real answer"


def test_chat_completion_sends_the_configured_model_temperature_and_thinking_switch():
    client, fake_openai, _ = _client(return_value=iter([_chunk("ok")]), model="some/other-model", temperature=0.7)

    client.chat_completion("p", enable_thinking=False)

    kwargs = fake_openai.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "some/other-model"
    assert kwargs["temperature"] == 0.7
    assert kwargs["stream"] is True
    assert kwargs["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
    assert kwargs["max_tokens"] == nim_client.MAX_TOKENS_FAST


def test_chat_completion_retries_the_whole_call_after_a_mid_stream_failure():
    """Seen live: a stream got a clean 200, ran for ten minutes, then the
    server errored mid-stream. The OpenAI client's own retries only cover the
    initial request, so the whole streamed call is redone."""
    client, fake_openai, sleep = _client(side_effect=[RuntimeError("Internal server error"), iter([_chunk("LINE: x")])])

    assert client.chat_completion("prompt") == "LINE: x"
    assert fake_openai.chat.completions.create.call_count == 2
    assert sleep.call_count == 1


def test_chat_completion_raises_after_exhausting_all_attempts():
    client, fake_openai, sleep = _client(side_effect=RuntimeError("Internal server error"), max_stream_attempts=3)

    with pytest.raises(RuntimeError, match="Internal server error"):
        client.chat_completion("prompt")

    assert fake_openai.chat.completions.create.call_count == 3
    assert sleep.call_count == 2  # one wait between each pair of attempts, none after the last


def test_a_zero_attempt_setting_still_makes_one_attempt():
    client, fake_openai, _ = _client(side_effect=RuntimeError("real error"), max_stream_attempts=0)

    with pytest.raises(RuntimeError, match="real error"):
        client.chat_completion("p")

    assert fake_openai.chat.completions.create.call_count == 1


def test_chat_completion_does_not_retry_a_bad_api_key():
    """A 401 is the request being wrong, not the server being flaky; retrying
    only delays the message the operator needs to see."""
    client, fake_openai, sleep = _client(side_effect=_api_error(openai.AuthenticationError, 401))

    with pytest.raises(openai.AuthenticationError):
        client.chat_completion("prompt")

    assert fake_openai.chat.completions.create.call_count == 1
    sleep.assert_not_called()


def test_chat_completion_retries_rate_limits():
    client, _, _ = _client(side_effect=[_api_error(openai.RateLimitError, 429), iter([_chunk("ok")])])

    assert client.chat_completion("prompt") == "ok"


def test_is_retryable_classification():
    assert is_retryable(_api_error(openai.InternalServerError, 500))
    assert is_retryable(openai.APIConnectionError(request=httpx.Request("POST", "https://nim.test")))
    assert is_retryable(RuntimeError("stream died"))
    assert not is_retryable(_api_error(openai.BadRequestError, 400))
    assert not is_retryable(_api_error(openai.NotFoundError, 404))


def test_backoff_grows_exponentially_with_jitter_and_is_capped():
    with patch.object(nim_client.random, "uniform", return_value=1.0):
        assert [backoff_seconds(a) for a in (1, 2, 3)] == [2.0, 4.0, 8.0]
        assert backoff_seconds(10) == nim_client.BACKOFF_MAX_SECONDS
    assert 1.0 <= backoff_seconds(1) <= 3.0


def test_retry_waits_outside_the_concurrency_slot():
    """While a call backs off it must not hold a stream slot: with a single
    slot, another call made during the wait still completes."""
    other_call_finished = threading.Event()
    holder = {}

    def sleep_by_making_another_call(_delay):
        def other():
            holder["result"] = holder["client"].chat_completion("other")
            other_call_finished.set()
        threading.Thread(target=other, daemon=True).start()
        assert other_call_finished.wait(timeout=5), "the slot was still held during backoff"

    client, _, _ = _client(
        side_effect=[RuntimeError("boom"), iter([_chunk("other-ok")]), iter([_chunk("ok")])],
        sleep=sleep_by_making_another_call, max_concurrent_requests=1,
    )
    holder["client"] = client

    assert client.chat_completion("prompt") == "ok"
    assert holder["result"] == "other-ok"


def test_chat_completion_caps_concurrent_requests():
    """The fan-out can request (claims x ensemble x targets) calls at once; the
    gate bounds how many streams are open while still letting every call complete."""
    counter_lock = threading.Lock()
    active = {"now": 0, "max": 0}
    # Each in-flight pair rendezvous here before either finishes. With the cap
    # at 2 that is exactly the two calls holding slots, so the test observes
    # true concurrency without sleep timing. A leaked third call would
    # register in `max`.
    pair = threading.Barrier(2, timeout=5)

    def fake_create(**kwargs):
        with counter_lock:
            active["now"] += 1
            active["max"] = max(active["max"], active["now"])
        pair.wait()
        with counter_lock:
            active["now"] -= 1
        return iter([_chunk("ok")])

    client, _, _ = _client(side_effect=fake_create, max_concurrent_requests=2)
    results = []
    threads = [threading.Thread(target=lambda: results.append(client.chat_completion("p"))) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results == ["ok"] * 6
    assert active["max"] == 2


def test_openai_client_is_built_once_and_reused(monkeypatch):
    """Connection reuse across the fan-out depends on one client per NimClient."""
    monkeypatch.setenv("NVIDIA_API_KEY", "k")
    client = NimClient()

    assert client._client() is client._client()


def test_missing_api_key_is_a_config_error_that_says_what_to_set(monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)

    with pytest.raises(ConfigError, match="NVIDIA_API_KEY"):
        NimClient()._client()


def test_configure_replaces_the_default_client_with_the_given_overrides():
    """Only non-None overrides apply; a 0.0 temperature is a real value."""
    configured = nim_client.configure(model="some/other-model", temperature=0.0,
                                      max_concurrent_requests=3, timeout_seconds=None)

    assert configured.settings == Settings(model="some/other-model", temperature=0.0, max_concurrent_requests=3)
    assert nim_client.max_concurrent_requests() == 3


def test_module_level_chat_completion_delegates_to_the_default_client():
    fake_openai = MagicMock()
    fake_openai.chat.completions.create.return_value = iter([_chunk("ok")])
    with patch.object(nim_client, "_default", NimClient(openai_client=fake_openai)):
        assert nim_client.chat_completion("p") == "ok"
