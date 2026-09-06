import os
from openai import OpenAI

MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b"

# A real triggered Action run observed one (correct, non-truncated) call
# taking ~19 minutes. That's a real cost of "thinking" mode, not a bug —
# so the timeout has to sit comfortably above that rather than fail a
# legitimate slow-but-right answer.
_TIMEOUT_SECONDS = 1500
_MAX_RETRIES = 1

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
    """
    client = get_client()
    response = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=_MAX_TOKENS_THINKING if enable_thinking else _MAX_TOKENS_FAST,
        extra_body={"chat_template_kwargs": {"enable_thinking": enable_thinking}},
    )
    return response.choices[0].message.content
