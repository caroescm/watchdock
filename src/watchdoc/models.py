"""The data contract shared by every stage of the pipeline.

A finding is created by the parser, gains a fix from the drafter and a
delivery status from whichever delivery path ran, then is read by the report.
Making that a dataclass, and the two closed vocabularies enums, means each
stage can rely on the fields instead of probing a dict with .get() defaults.
"""
from dataclasses import dataclass
from enum import StrEnum


class Origin(StrEnum):
    """Who authored the PR; decides how fixes are delivered."""
    AGENT = "agent"   # fixes are committed straight onto the branch
    HUMAN = "human"   # fixes are posted as one-click review suggestions


class Delivery(StrEnum):
    """How one finding's fix reached the PR."""
    COMMITTED = "committed"
    SUGGESTION_POSTED = "suggestion_posted"
    FALLBACK_COMMENT = "fallback_comment"
    NOT_APPLIED_NO_MATCH = "not_applied_no_match"
    COMMIT_FAILED = "commit_failed"


@dataclass
class Finding:
    """One stale line in one target file.

    `line`, `reason` and `type` come from the model's LINE/TYPE/REASON block.
    `fix` is filled in by fixes.draft_fix and `delivery` by the delivery step;
    both are None until then.
    """
    line: str
    reason: str
    type: str = "drift"
    fix: str | None = None
    delivery: Delivery | None = None
