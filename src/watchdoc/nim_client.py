"""Every model call in Watchdoc goes through one NimClient.

A NimClient owns its settings, its concurrency gate and its lazily built
OpenAI-compatible client, so a run's configuration is one immutable object
rather than a set of module variables. The module-level ``chat_completion``
and ``configure`` functions are a facade over a single default instance for
call sites that don't want to carry a client around; ``configure`` swaps
the whole instance in one assignment, so a call already in flight keeps the
client, gate and settings it started with.

Two rules, both measured on live runs (project-docs/BENCHMARK.md,
"Operational findings from live runs"):

- Thinking stays on by default. Turning it off is 20-100x faster and cost
  29 points of recall on the benchmark; only a genuinely mechanical call
  site (fixes.draft_fix) should opt out.
- Every call streams. Non-streaming calls were reset by an idle timeout
  between here and NIM; a streamed replay of the same call completed.
"""
import logging
import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace

import openai
from openai import OpenAI

from watchdoc import env

logger = logging.getLogger(__name__)

MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"
BASE_URL = "https://integrate.api.nvidia.com/v1"

# Deterministic decoding is the benchmark's measured setting. The model is
# still observably nondeterministic at 0.0, which is why claims.py runs an
# ensemble; raising it is a config decision, not a call-site one.
TEMPERATURE = 0.0

# Must exceed the longest correct thinking-mode answer observed (~19 min).
TIMEOUT_SECONDS = 1500

# NVIDIA's free-tier concurrency limit is undocumented; a handful of
# simultaneous streams is the most ever proven safe in a real run. Waiters
# queue on the gate, so nothing is abandoned and recall is unaffected.
MAX_CONCURRENT_REQUESTS = 8

# Whole-call attempts. The OpenAI client's own retries (CLIENT_MAX_RETRIES)
# only cover failures of the initial request; a stream that fails after a
# 200 has to be redone from the start, since a partial stream can't resume.
MAX_STREAM_ATTEMPTS = 2
CLIENT_MAX_RETRIES = 3

# Thinking calls need room for the chain of thought plus the answer;
# mechanical (thinking-off) calls need very little.
MAX_TOKENS_THINKING = 24000
MAX_TOKENS_FAST = 800

# Wait between whole-call attempts: 2s, 4s, 8s ... capped, with jitter so the
# fan-out doesn't retry in lockstep against a server that just failed. The
# wait happens outside the concurrency slot.
BACKOFF_BASE_SECONDS = 2.0
BACKOFF_MAX_SECONDS = 30.0

# Errors no retry can fix: the request itself is wrong (bad key, forbidden
# model, malformed body). Everything else, including connection resets,
# timeouts, 429s, 5xx and the generic exceptions a stream raises when the
# server dies mid-response, is worth another attempt.
NON_RETRYABLE = (
    openai.AuthenticationError,
    openai.PermissionDeniedError,
    openai.BadRequestError,
    openai.NotFoundError,
    openai.UnprocessableEntityError,
)


@dataclass(frozen=True)
class Settings:
    """Everything an operator can tune about the model calls. The defaults
    are the module constants above; the NAT workflow config overrides them."""
    model: str = MODEL
    temperature: float = TEMPERATURE
    timeout_seconds: float = TIMEOUT_SECONDS
    max_concurrent_requests: int = MAX_CONCURRENT_REQUESTS
    max_stream_attempts: int = MAX_STREAM_ATTEMPTS
    base_url: str = BASE_URL


DEFAULT_SETTINGS = Settings()


def is_retryable(error: BaseException) -> bool:
    return not isinstance(error, NON_RETRYABLE)


def backoff_seconds(attempt: int) -> float:
    base = min(BACKOFF_BASE_SECONDS * 2 ** (attempt - 1), BACKOFF_MAX_SECONDS)
    return base * random.uniform(0.5, 1.5)


class NimClient:
    """One client per run: settings, concurrency gate, pooled connections.

    ``openai_client`` and ``sleep`` are injectable so tests can drive the
    retry loop with a fake stream and no real waiting.
    """

    def __init__(
        self,
        settings: Settings = DEFAULT_SETTINGS,
        *,
        openai_client: OpenAI | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self._slots = threading.BoundedSemaphore(settings.max_concurrent_requests)
        self._openai = openai_client
        self._openai_lock = threading.Lock()
        self._sleep = sleep

    @property
    def max_concurrent_requests(self) -> int:
        """Callers sizing a thread pool should not exceed this; extra threads
        would only queue on the gate."""
        return self.settings.max_concurrent_requests

    def _client(self) -> OpenAI:
        """Built once, so connections are pooled across the fan-out instead
        of a fresh client (and TLS handshake) per call."""
        with self._openai_lock:
            if self._openai is None:
                self._openai = OpenAI(
                    base_url=self.settings.base_url,
                    api_key=env.require("NVIDIA_API_KEY",
                                        "Add your build.nvidia.com key as the NVIDIA_API_KEY secret."),
                    timeout=self.settings.timeout_seconds,
                    max_retries=CLIENT_MAX_RETRIES,
                )
            return self._openai

    def chat_completion(self, prompt: str, enable_thinking: bool = True) -> str:
        """One streamed call, retried whole on retryable failure, bounded by
        the concurrency gate while the stream is open."""
        attempts = max(1, self.settings.max_stream_attempts)  # always try at least once
        for attempt in range(1, attempts + 1):
            try:
                with self._slots:
                    return self._stream_once(prompt, enable_thinking)
            except Exception as e:  # noqa: BLE001 — classified just below
                if not is_retryable(e) or attempt == attempts:
                    raise
                delay = backoff_seconds(attempt)
                logger.warning("chat_completion attempt %d/%d failed (%s); retrying in %.1fs",
                               attempt, attempts, type(e).__name__, delay, exc_info=True)
                self._sleep(delay)
        raise AssertionError("unreachable: the loop returns or raises")

    def _stream_once(self, prompt: str, enable_thinking: bool) -> str:
        stream = self._client().chat.completions.create(
            model=self.settings.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=self.settings.temperature,
            max_tokens=MAX_TOKENS_THINKING if enable_thinking else MAX_TOKENS_FAST,
            extra_body={"chat_template_kwargs": {"enable_thinking": enable_thinking}},
            stream=True,
        )
        # Only `content` deltas are the answer; thinking tokens land elsewhere.
        chunks = []
        for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta is not None and delta.content:
                chunks.append(delta.content)
        return "".join(chunks)


# ---- Facade over one process-wide default client -------------------------

_default = NimClient()


def configure(**overrides: object) -> NimClient:
    """Replaces the default client with one built from DEFAULT_SETTINGS plus
    the given non-None overrides (any Settings field name). Call once, before
    the run's first chat_completion."""
    global _default
    settings = replace(DEFAULT_SETTINGS, **{k: v for k, v in overrides.items() if v is not None})
    _default = NimClient(settings)
    return _default


def default_client() -> NimClient:
    return _default


def chat_completion(prompt: str, enable_thinking: bool = True) -> str:
    return _default.chat_completion(prompt, enable_thinking)


def max_concurrent_requests() -> int:
    return _default.max_concurrent_requests
