import os
from openai import OpenAI

MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"

# A real triggered Action run observed one (correct, non-truncated) call
# taking ~19 minutes. That's a real cost of "thinking" mode, not a bug —
# so the timeout has to sit comfortably above that rather than fail a
# legitimate slow-but-right answer.
_TIMEOUT_SECONDS = 1500

# Two separate triggered runs both hit a connection reset around the
# 4-4.5 minute mark of a call — once on a single extract_claims call
# (recovered on its one retry), once on all 3 parallel ensemble calls at
# once (didn't recover, crashed the whole run). Same timing both times,
# with and without concurrency, so this looks like a real recurring
# reset rather than one-off noise. Raised from 1 so a single reset isn't
# one unlucky retry away from taking down the whole ensemble again.
_MAX_RETRIES = 3

# With thinking enabled, a real diagnosed failure showed the model correctly
# reasoning through a case in detail, then running out of budget mid-thought
# — before ever emitting the actual structured answer. 8000 wasn't enough;
# NVIDIA's own reference examples budget 16384 for the chain-of-thought
# alone, on top of the final answer, so we go higher still to leave real
# headroom. Mechanical (thinking-off) calls need far less room.
_MAX_TOKENS_THINKING = 24000
_MAX_TOKENS_FAST = 800


def get_client():
    return OpenAI(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=os.environ["NVIDIA_API_KEY"],
        timeout=_TIMEOUT_SECONDS,
        max_retries=_MAX_RETRIES,
    )


def chat_completion(prompt, enable_thinking=True):
    """Single shared entry point for every NIM call in this project.

    enable_thinking defaults to True: a direct 9-case comparison showed that
    disabling it recovered 20-100x speed but silently dropped full-pipeline
    recall from 100% to 71%, missing exactly the cases that need real
    inference (a file rename invalidating a reference, a flag's scope
    narrowing without disappearing) — not truncation (raising max_tokens
    4x did not recover the misses). Thinking mode costs real latency but is
    doing real reasoning work; only opt out per call site for genuinely
    mechanical tasks (see fixes.draft_fix) where that trade is safe.

    Streams the response rather than waiting for one non-streaming reply.
    Two triggered runs both saw a *non-streaming* call get its connection
    reset by something between here and NVIDIA's server after ~4.3-4.6
    minutes of silence, every retry included — consistent with an
    intermediate timeout that kills a connection with no bytes flowing.
    A direct replay of the exact same failing call, streamed, ran a full
    411s with zero disconnects and a complete, correct answer: bytes were
    still arriving the whole time (thinking-mode tokens land in a separate
    reasoning field, not `content`, so nothing above needs to change to
    read them — only `content` deltas are accumulated, same as what
    `.message.content` already returned in the non-streaming version).
    """
    client = get_client()
    stream = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=_MAX_TOKENS_THINKING if enable_thinking else _MAX_TOKENS_FAST,
        extra_body={"chat_template_kwargs": {"enable_thinking": enable_thinking}},
        stream=True,
    )
    chunks = []
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
            chunks.append(chunk.choices[0].delta.content)
    return "".join(chunks)
