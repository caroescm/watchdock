import logging
import os
import random
import threading
import time

import openai
from openai import OpenAI

logger = logging.getLogger(__name__)

MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"

# Deterministic decoding is the benchmark's measured setting. The model is
# still observably nondeterministic at 0.0 (hence the ensemble), but raising
# it is a config decision, not something to bury in the call site.
TEMPERATURE = 0.0

# The values below were each set in response to a specific failure seen in
# a live run. The rule is stated here; the incident behind it is recorded in
# project-docs/BENCHMARK.md, "Operational findings from live runs".

# Must exceed the longest correct thinking-mode answer observed (~19 min).
_TIMEOUT_SECONDS = 1500

# The OpenAI client's own retries, covering failures of the initial request.
# One was not enough to survive the connection resets NIM produces.
_MAX_RETRIES = 3

# Thinking calls need room for the chain of thought plus the answer;
# mechanical (thinking-off) calls need very little.
_MAX_TOKENS_THINKING = 24000
_MAX_TOKENS_FAST = 800

# Whole-call attempts. The client's retries never see a stream that fails
# after a 200, so the entire streamed call is redone; a partial stream
# cannot be resumed.
_MAX_STREAM_RETRIES = 2

# Wait between whole-call attempts: 2s, 4s, 8s ... capped, with jitter so
# the (claims x ensemble) fan-out doesn't retry in lockstep against a
# server that just failed. The wait happens *outside* the concurrency
# slot, so a call that is backing off isn't holding a stream open for
# nobody.
_BACKOFF_BASE_SECONDS = 2.0
_BACKOFF_MAX_SECONDS = 30.0
_sleep = time.sleep  # indirection so tests can skip the wait

# Errors that no retry can fix: the request itself is wrong (bad key,
# forbidden model, malformed body). Everything else — connection resets,
# timeouts, 429s, 5xx, and the generic exceptions a stream raises when the
# server dies mid-response — is worth another attempt.
_NON_RETRYABLE = (
    openai.AuthenticationError,
    openai.PermissionDeniedError,
    openai.BadRequestError,
    openai.NotFoundError,
    openai.UnprocessableEntityError,
)

# Per-claim decomposition (see claims.check_claims_against_target) can put
# (claims x 3 ensemble samples x targets) requests in flight at once — e.g.
# 8 claims against 2 targets is 48 calls. NVIDIA's free-tier concurrency
# limit is undocumented; the most ever proven safe in a real run is a
# handful of simultaneous streams. This gate bounds how many are open at
# once (waiters just queue — every call still runs to completion, nothing
# is abandoned, so it can't cost recall the way the reverted capped-wait
# ensemble did). Tuned from the workflow config, not the environment.
MAX_CONCURRENT_REQUESTS = 8

BASE_URL = "https://integrate.api.nvidia.com/v1"

# Runtime settings. The module constants above are the defaults; the NAT
# entry point overrides them from its workflow config via configure(), so the
# model, timeout and concurrency cap are tunable from config.yml rather than
# only by editing this file.
_settings = {
    "model": MODEL,
    "temperature": TEMPERATURE,
    "timeout_seconds": _TIMEOUT_SECONDS,
    "max_concurrent_requests": MAX_CONCURRENT_REQUESTS,
}
_request_slots = threading.BoundedSemaphore(MAX_CONCURRENT_REQUESTS)
_client = None
_client_lock = threading.Lock()


def max_concurrent_requests():
    """The configured cap on simultaneously open NIM streams. Callers sizing a
    thread pool should not exceed it: extra threads only queue on the gate."""
    return _settings["max_concurrent_requests"]


def configure(model=None, temperature=None, timeout_seconds=None, max_concurrent_requests=None):
    """Applies workflow-level settings. Call once, before any chat_completion."""
    global _request_slots, _client
    with _client_lock:
        if model:
            _settings["model"] = model
        if temperature is not None:
            _settings["temperature"] = temperature
        if timeout_seconds:
            _settings["timeout_seconds"] = timeout_seconds
        if max_concurrent_requests:
            _settings["max_concurrent_requests"] = max_concurrent_requests
            _request_slots = threading.BoundedSemaphore(max_concurrent_requests)
        _client = None  # rebuilt lazily with the new timeout


def get_client():
    """One OpenAI client per process, so connections are pooled and reused
    across the (claims x ensemble x targets) fan-out instead of a fresh
    client (and TLS handshake) per call."""
    global _client
    with _client_lock:
        if _client is None:
            _client = OpenAI(
                base_url=BASE_URL,
                api_key=os.environ["NVIDIA_API_KEY"],
                timeout=_settings["timeout_seconds"],
                max_retries=_MAX_RETRIES,
            )
        return _client


def chat_completion(prompt, enable_thinking=True):
    """Single shared entry point for every NIM call in this project.

    Two rules, both measured (see project-docs/BENCHMARK.md, "Operational findings from live runs"):

    - Thinking stays on by default. Turning it off is 20-100x faster and
      cost 29 points of recall on the benchmark; only a genuinely mechanical
      call site (fixes.draft_fix) should opt out.
    - Every call streams. Non-streaming calls were reset by an idle timeout
      between here and NIM; a streamed replay of the same call completed.
      Only `content` deltas are accumulated; thinking tokens land elsewhere.
    """
    client = get_client()
    attempts = max(1, _MAX_STREAM_RETRIES)  # always try at least once; never fall off the loop
    for attempt in range(1, attempts + 1):
        try:
            with _request_slots:
                return _stream_once(client, prompt, enable_thinking)
        except Exception as e:  # noqa: BLE001 — classified just below
            if not _is_retryable(e) or attempt == attempts:
                raise
            delay = _backoff_seconds(attempt)
            logger.warning("chat_completion attempt %d/%d failed (%s); retrying in %.1fs",
                           attempt, attempts, type(e).__name__, delay, exc_info=True)
            _sleep(delay)


def _stream_once(client, prompt, enable_thinking):
    stream = client.chat.completions.create(
        model=_settings["model"],
        messages=[{"role": "user", "content": prompt}],
        temperature=_settings["temperature"],
        max_tokens=_MAX_TOKENS_THINKING if enable_thinking else _MAX_TOKENS_FAST,
        extra_body={"chat_template_kwargs": {"enable_thinking": enable_thinking}},
        stream=True,
    )
    chunks = []
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
            chunks.append(chunk.choices[0].delta.content)
    return "".join(chunks)


def _is_retryable(error):
    return not isinstance(error, _NON_RETRYABLE)


def _backoff_seconds(attempt):
    base = min(_BACKOFF_BASE_SECONDS * 2 ** (attempt - 1), _BACKOFF_MAX_SECONDS)
    return base * random.uniform(0.5, 1.5)
