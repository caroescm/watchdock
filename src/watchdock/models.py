"""The data contract shared by every stage of the pipeline.

Every value that crosses a stage boundary is a frozen dataclass or an enum.
A stage that adds information (a drafted fix, a delivery status) returns a
new object via ``dataclasses.replace`` instead of mutating the one it was
given, so no stage can observe a half-filled object from another.
"""
from dataclasses import dataclass
from enum import StrEnum


class Origin(StrEnum):
    """Who authored the PR."""
    AGENT = "agent"
    HUMAN = "human"


class DeliveryMode(StrEnum):
    """How this run delivers fixes. Resolved once from the PR origin and the
    ``commit_fixes`` setting, then passed down; no later stage re-derives it."""
    COMMIT = "commit"     # fixes are committed straight onto the PR branch
    SUGGEST = "suggest"   # fixes are posted as one-click review suggestions


class Delivery(StrEnum):
    """How one finding's fix reached the PR."""
    COMMITTED = "committed"
    SUGGESTION_POSTED = "suggestion_posted"
    # No one-click suggestion could be attached (line outside the PR diff, or
    # not found verbatim); the fix is carried by the run summary alone.
    IN_SUMMARY = "in_summary"
    NOT_APPLIED_NO_MATCH = "not_applied_no_match"
    NOT_APPLIED_NO_CHANGE = "not_applied_no_change"
    # The commit was refused (fork PR, read-only token, moved branch) and the
    # fix was re-delivered through the suggestion path, which itself may have
    # ended as a review suggestion or as a summary-only entry.
    COMMIT_FAILED = "commit_failed"


class FindingType(StrEnum):
    """The two kinds of drift the check prompt asks the model to distinguish."""
    SEMANTIC_STALENESS = "semantic staleness"
    BROKEN_REFERENCE = "broken reference"

    @classmethod
    def from_model_output(cls, text: str | None) -> "FindingType":
        """Maps the model's free-text TYPE value onto the closed vocabulary.
        Anything that doesn't name a broken reference is semantic staleness;
        the prompt tells the model the label is a minor detail, so a fuzzy
        mapping is the right level of strictness."""
        return cls.BROKEN_REFERENCE if "broken" in (text or "").lower() else cls.SEMANTIC_STALENESS


class SummaryOutcome(StrEnum):
    """What happened to the per-run summary comment."""
    POSTED = "summary_posted"
    UPDATED = "summary_updated"
    FAILED = "summary_failed"


@dataclass(frozen=True)
class DiffEntry:
    """One changed file in a PR: its path and its unified-diff patch text."""
    filename: str
    patch: str


@dataclass(frozen=True)
class Finding:
    """One stale line in one target file.

    ``line``, ``reason`` and ``kind`` come from the model's LINE/TYPE/REASON
    block. ``fix`` is added by the drafting stage and ``delivery`` by the
    delivery stage; both are None until then.
    """
    line: str
    reason: str
    kind: FindingType = FindingType.SEMANTIC_STALENESS
    fix: str | None = None
    delivery: Delivery | None = None
