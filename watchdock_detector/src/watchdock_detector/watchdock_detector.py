"""NeMo Agent Toolkit entry point for Watchdock.

NAT's role here is deliberate and limited: it is the host. It registers this
function, parses ``config.yml`` into :class:`WatchdockDetectorFunctionConfig`,
and runs it via ``nat run``. The model calls themselves go through
``watchdock.nim_client`` (an OpenAI-compatible client against NVIDIA NIM), not
NAT's ``llms:`` abstraction, because the pipeline depends on two things that
abstraction does not expose: the per-call Nemotron ``enable_thinking`` switch
sent as ``chat_template_kwargs``, and whole-call retries around a streamed
response. Everything an operator would want to tune is therefore a field on
the function config below.

The pipeline itself is ``watchdock.pipeline.run_pipeline``: synchronous code
with no NAT dependency, run in a worker thread via ``asyncio.to_thread`` so
NAT's event loop is never blocked.
"""
import asyncio
from dataclasses import replace

from nat.plugin_api import Builder, FunctionBaseConfig, FunctionInfo, register_function
from pydantic import Field

from watchdock import nim_client
from watchdock.claims import DEFAULT_ENSEMBLE_SIZE
from watchdock.pipeline import RunOptions, parse_pr_number, resolve_repo_root, run_pipeline

_DEFAULTS = nim_client.DEFAULT_SETTINGS


class WatchdockDetectorFunctionConfig(FunctionBaseConfig, name="watchdock_detector"):
    """
    Watchdock drift detector: checks docs and AI-agent instruction files for semantic drift against a PR diff.
    """

    repo_root: str | None = Field(
        default=None,
        description="Checkout to scan for target files. Defaults to $GITHUB_WORKSPACE (the "
                    "calling repository inside a GitHub Action), then the current directory.",
    )
    model: str = Field(
        default=_DEFAULTS.model,
        description="NIM model id used for every call.",
    )
    temperature: float = Field(
        default=_DEFAULTS.temperature, ge=0.0, le=2.0,
        description="Sampling temperature. 0.0 is the setting the benchmark was measured at.",
    )
    ensemble_size: int = Field(
        default=DEFAULT_ENSEMBLE_SIZE, ge=1,
        description="Independent samples per claim/target check; findings are unioned.",
    )
    max_concurrent_nim_calls: int = Field(
        default=_DEFAULTS.max_concurrent_requests, ge=1,
        description="Upper bound on simultaneously open NIM streams.",
    )
    nim_timeout_seconds: int = Field(
        default=int(_DEFAULTS.timeout_seconds), ge=1,
        description="Per-request timeout. Thinking-mode calls have been observed to take ~19 minutes.",
    )
    commit_fixes: bool = Field(
        default=True,
        description="Commit fixes directly onto agent-authored PR branches. Needs `contents: write`; "
                    "set false to deliver every fix as a review suggestion and drop that permission.",
    )


@register_function(config_type=WatchdockDetectorFunctionConfig)
async def watchdock_detector_function(config: WatchdockDetectorFunctionConfig, builder: Builder):
    """
    Registers the Watchdock drift-detection workflow (addressable via `watchdock_detector` in configuration).
    """
    nim_client.configure(
        model=config.model,
        temperature=config.temperature,
        timeout_seconds=config.nim_timeout_seconds,
        max_concurrent_requests=config.max_concurrent_nim_calls,
    )
    repo_root = resolve_repo_root(config.repo_root)
    options = RunOptions(
        ensemble_size=config.ensemble_size,
        max_workers=config.max_concurrent_nim_calls,
        commit_fixes=config.commit_fixes,
    )

    async def run_watchdock_check(task: str) -> str:
        """
        Runs the full Watchdock pipeline against a PR: discovers target files,
        fetches the diff, extracts claims, checks each target for drift, and for
        every real finding drafts a fix and delivers it — as a suggestion comment
        for human-authored PRs, or a direct commit for agent-authored PRs. The PR
        comes from the GitHub event payload, or from a "#<number>" in the input.
        Returns the summary posted to the PR.
        """
        return await asyncio.to_thread(
            run_pipeline, repo_root, replace(options, pr_number=parse_pr_number(task)))

    yield FunctionInfo.from_fn(run_watchdock_check, description=run_watchdock_check.__doc__)
