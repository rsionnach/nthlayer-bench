"""Post-incident review — chronological timeline + accuracy + worked/improve.

Walks the full verdict chain from ``case.underlying_verdict`` and assembles
a structured ``PostIncidentReview`` covering:

- chronological timeline (asc by ``created_at``)
- worked vs. to-improve classification (from outcome_resolution outcomes)
- per-verdict accuracy (confidence vs. resolved outcome)

Every field traces to a verdict — no LLM call. Optional action-item
suggestion (inventory §5) is off by default and deferred to a follow-up.

Spec: ``docs/superpowers/specs/2026-04-26-respond-sre-cli-inventory-for-bench.md``
section 5 (post-incident).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from nthlayer_common.api_client import CoreAPIClient

from nthlayer_bench.sre.brief import (
    AnchorVerdictMissingError,
    BriefError,
    CaseNotFoundError,
    CoreUnreachableError,
)

ReviewState = Literal["in_progress", "resolved"]


@dataclass
class TimelineEntry:
    """One row in the chronological timeline."""

    verdict_id: str
    timestamp: str            # ISO 8601 from verdict.created_at
    actor: str                # producer.system, e.g. "nthlayer-respond"
    role: str                 # subject.type, e.g. "triage"
    summary: str              # subject.summary or judgment.reasoning fallback
    confidence: float | None  # judgment.confidence, may be None


@dataclass
class VerdictAccuracy:
    """Accuracy record for a single verdict.

    outcome_status is populated from either pattern:
    - lineage-style: latest outcome_resolution child verdict's status (existing behaviour)
    - mutation-style: the original verdict's own outcome.status (opensrm-jmy.19)

    When mutation-style outcome.status == "overridden", the override_*
    fields below capture the operator attribution from outcome.override.
    Both patterns coexist; the renderer surfaces both when both exist.
    """

    verdict_id: str
    role: str                  # original verdict's subject.type
    confidence: float | None   # original verdict's judgment.confidence
    outcome_status: str | None  # confirmed | overridden | partial | superseded | expired
    # NEW (jmy.19) — populated only when the original verdict has been
    # mutation-style overridden (outcome.status=="overridden" with a
    # non-empty outcome.override). All-None for non-override outcomes
    # and for lineage-style-only resolutions.
    override_by: str | None = None
    override_at: str | None = None
    override_action: str | None = None
    override_reasoning: str | None = None
    override_original_action: str | None = None


@dataclass
class PostIncidentReview:
    """Full structured review, ready to render or export."""

    case_id: str
    service: str
    severity: int | None
    state: ReviewState
    duration_minutes: int | None  # case.created_at → latest verdict; None if no chain
    timeline: list[TimelineEntry] = field(default_factory=list)
    worked: list[str] = field(default_factory=list)        # human-readable bullets
    to_improve: list[str] = field(default_factory=list)    # human-readable bullets
    accuracy: list[VerdictAccuracy] = field(default_factory=list)


class PostIncidentError(BriefError):
    """Base class for post-incident errors. Inherits from BriefError so
    the same widget-level catch covers both brief and review surfaces."""


# Re-exports for one-stop import: callers can pull all post-incident
# names plus the shared brief error hierarchy from this single module.
__all__ = [
    "PostIncidentReview",
    "ReviewState",
    "TimelineEntry",
    "VerdictAccuracy",
    "PostIncidentError",
    "CaseNotFoundError",
    "AnchorVerdictMissingError",
    "CoreUnreachableError",
    "build_post_incident_review",
    "render_post_incident_review",
]


# ------------------------------------------------------------------ #
# Data fetch (mirrors sre/brief.py shape)                              #
# ------------------------------------------------------------------ #

async def _get_case(client: CoreAPIClient, case_id: str) -> dict:
    result = await client.get_case(case_id)
    if result.ok:
        return result.data
    if result.status_code == 404:
        raise CaseNotFoundError(case_id)
    if result.status_code == 0:
        raise CoreUnreachableError(result.detail or {"error": result.error})
    raise PostIncidentError(
        f"get_case failed: {result.error} (status={result.status_code})"
    )


async def _get_anchor(client: CoreAPIClient, anchor_id: str) -> dict:
    result = await client.get_verdict(anchor_id)
    if result.ok:
        return result.data
    if result.status_code == 404:
        raise AnchorVerdictMissingError(anchor_id)
    if result.status_code == 0:
        raise CoreUnreachableError(result.detail or {"error": result.error})
    raise PostIncidentError(
        f"get_verdict failed: {result.error} (status={result.status_code})"
    )


async def _get_descendants(client: CoreAPIClient, anchor_id: str) -> list[dict]:
    result = await client.get_descendants(anchor_id)
    if result.ok:
        return result.data or []
    if result.status_code == 0:
        raise CoreUnreachableError(result.detail or {"error": result.error})
    raise PostIncidentError(
        f"get_descendants failed: {result.error} (status={result.status_code})"
    )


# ------------------------------------------------------------------ #
# Projections                                                          #
# ------------------------------------------------------------------ #

def _verdict_summary(v: dict) -> str:
    """Single source of truth for the per-verdict display summary.

    ``subject.summary`` first; falls back to ``judgment.reasoning`` if
    summary is missing or empty. Used by both the timeline entry and the
    worked/to-improve classifier so the two surfaces never diverge.
    """
    subject = v.get("subject") or {}
    judgment = v.get("judgment") or {}
    return subject.get("summary") or judgment.get("reasoning", "")


def _is_outcome_resolution(v: dict) -> bool:
    """Detect outcome_resolution verdicts robustly across core variants.

    Some core deployments carry the type on ``verdict_type`` rather than
    ``subject.type``; check both so the matching logic doesn't silently
    miss outcomes when the producer evolves.
    """
    if (v.get("subject") or {}).get("type") == "outcome_resolution":
        return True
    return v.get("verdict_type") == "outcome_resolution"


def _verdict_to_entry(v: dict) -> TimelineEntry:
    subject = v.get("subject") or {}
    judgment = v.get("judgment") or {}
    producer = v.get("producer") or {}
    return TimelineEntry(
        verdict_id=v.get("id", ""),
        timestamp=v.get("created_at", ""),
        actor=producer.get("system", "unknown"),
        role=subject.get("type", ""),
        summary=_verdict_summary(v),
        confidence=judgment.get("confidence"),
    )


def _build_accuracy(chain: list[dict]) -> list[VerdictAccuracy]:
    """For each non-outcome verdict, emit a VerdictAccuracy combining:

    - lineage-style: latest outcome_resolution child status (existing)
    - mutation-style: the original verdict's outcome.status and
      outcome.override fields (jmy.19)

    When both signals exist for the same verdict, both are surfaced:
    the lineage child wins for outcome_status; the override_* fields
    are populated from outcome.override regardless. When only the
    mutation-style signal exists, it fills in outcome_status as a
    fallback (mirrors the spec § 2 contract that the original is
    authoritative for operator-imposed amendments).

    Outcome resolutions carry ``parent_ids: [original_id]`` per core's
    immutable verdict contract. Multiple outcome resolutions for the
    same original are possible (resupersession); the latest one wins.
    """
    # Map original_id → latest outcome_resolution (by created_at).
    outcomes_for: dict[str, dict] = {}
    for v in chain:
        if not _is_outcome_resolution(v):
            continue
        # Skip outcomes with no created_at — they can't be reliably
        # ordered, and the latest-wins tiebreaker would silently pick
        # them ahead of well-formed predecessors via empty-string compare.
        if not v.get("created_at"):
            continue
        for parent in v.get("parent_ids") or []:
            existing = outcomes_for.get(parent)
            if existing is None or _sort_asc_key(v) > _sort_asc_key(existing):
                outcomes_for[parent] = v

    accuracy: list[VerdictAccuracy] = []
    for v in chain:
        if _is_outcome_resolution(v):
            continue
        original_id = v.get("id", "")
        if not original_id:
            continue

        # Lineage-style outcome_status (existing behaviour).
        outcome = outcomes_for.get(original_id)
        outcome_status = None
        if outcome is not None:
            outcome_status = outcome.get("outcome_status") or (
                (outcome.get("outcome") or {}).get("status")
            )

        # Mutation-style outcome.status + outcome.override (jmy.19).
        # The original verdict's outcome dict may carry status="overridden"
        # and a populated override dict. The `or {}` falls absent/None
        # outcome and override blobs through to an empty dict so the
        # downstream `.get()` calls return None natively.
        own_outcome = v.get("outcome") or {}
        own_status = own_outcome.get("status")
        override = own_outcome.get("override") or {}
        override_by = override.get("by")
        override_at = override.get("at")
        override_action = override.get("action")
        override_reasoning = override.get("reasoning")
        override_original_action = override.get("original_action")

        # Mutation-style fallback: when there's no lineage outcome child,
        # promote outcome.status="overridden" to outcome_status — but only
        # if override.by is also set (empty override dict = malformed
        # payload, not a real override; spec § 2 contract).
        if outcome_status is None and own_status == "overridden" and override_by:
            outcome_status = "overridden"

        judgment = v.get("judgment") or {}
        accuracy.append(
            VerdictAccuracy(
                verdict_id=original_id,
                role=(v.get("subject") or {}).get("type", ""),
                confidence=judgment.get("confidence"),
                outcome_status=outcome_status,
                override_by=override_by,
                override_at=override_at,
                override_action=override_action,
                override_reasoning=override_reasoning,
                override_original_action=override_original_action,
            )
        )
    return accuracy


def _classify(
    accuracy: list[VerdictAccuracy], chain: list[dict]
) -> tuple[list[str], list[str]]:
    """Convert per-verdict accuracy into worked/to_improve bullets.

    Heuristic: confirmed outcome → worked; overridden → to_improve;
    other statuses (partial, superseded, expired, None) skipped — surface
    only clear signals so the bullets stay short and operator-actionable.
    Summary text comes from :func:`_verdict_summary` so timeline and
    classification never diverge on the fallback rule.
    """
    worked: list[str] = []
    to_improve: list[str] = []
    chain_by_id = {v.get("id", ""): v for v in chain}
    for record in accuracy:
        v = chain_by_id.get(record.verdict_id)
        if v is None:
            continue
        line = f"{record.role}: {_verdict_summary(v)}".strip(": ").strip()
        if not line:
            # Skip degenerate verdicts (empty role + empty summary) so
            # we don't render bare "- " bullets to the operator.
            continue
        if record.outcome_status == "confirmed":
            worked.append(line)
        elif record.outcome_status == "overridden":
            to_improve.append(line)
    return worked, to_improve


def _sort_asc_key(v: dict) -> tuple[str, str]:
    # Ascending sort key: earliest first, ties broken by verdict_id.
    return (v.get("created_at", ""), v.get("id", ""))


def _compute_duration_minutes(case: dict, chain: list[dict]) -> int | None:
    if not chain:
        return None
    case_created = case.get("created_at")
    last_event = max((v.get("created_at", "") for v in chain), default="")
    if not case_created or not last_event:
        return None
    try:
        start = datetime.fromisoformat(case_created.replace("Z", "+00:00"))
        end = datetime.fromisoformat(last_event.replace("Z", "+00:00"))
        delta = end - start
    except (ValueError, AttributeError, TypeError):
        # ValueError: malformed ISO 8601 string.
        # AttributeError: non-string created_at (e.g. None survives upstream
        # guard).
        # TypeError: mixed naive/aware datetimes during subtraction —
        # arises from migrated/legacy payloads where one side has a tz
        # offset and the other doesn't. Failing closed (return None)
        # surfaces "duration unknown" rather than crashing the panel.
        return None
    return max(0, int(delta.total_seconds() // 60))


def _derive_state(case: dict) -> ReviewState:
    return "resolved" if case.get("state") == "resolved" else "in_progress"


def _derive_severity(chain: list[dict]) -> int | None:
    # Severity comes from the latest triage verdict's metadata.custom —
    # latest wins on re-triage so the operator sees the current authoritative
    # severity, not the original (potentially superseded) call. Defensive
    # int check: a string-typed severity from a deserialisation slip would
    # otherwise reach the renderer's `f"P{n}"` format and produce garbled
    # output ("PSEV-2"). Mirror brief.py's policy: never fabricate a
    # severity, never trust a non-int.
    triage_verdicts = [
        v for v in chain
        if (v.get("subject") or {}).get("type") == "triage"
    ]
    if not triage_verdicts:
        return None
    latest = max(triage_verdicts, key=_sort_asc_key)
    custom = (latest.get("metadata") or {}).get("custom") or {}
    severity = custom.get("severity")
    return severity if isinstance(severity, int) else None


# ------------------------------------------------------------------ #
# Public API                                                           #
# ------------------------------------------------------------------ #

async def build_post_incident_review(
    client: CoreAPIClient,
    case_id: str,
) -> PostIncidentReview:
    """Build a structured review for the case.

    Returns a partial review (state="in_progress") for cases that are
    still open — operators can peek at the timeline as it builds. Fully
    resolved cases get state="resolved" and a complete review.
    """
    case = await _get_case(client, case_id)
    anchor_id = case["underlying_verdict"]
    service = case.get("service") or "unknown"

    anchor = await _get_anchor(client, anchor_id)
    descendants = await _get_descendants(client, anchor_id)

    chain = sorted([anchor, *descendants], key=_sort_asc_key)

    timeline = [_verdict_to_entry(v) for v in chain]
    accuracy = _build_accuracy(chain)
    worked, to_improve = _classify(accuracy, chain)

    return PostIncidentReview(
        case_id=case_id,
        service=service,
        severity=_derive_severity(chain),
        state=_derive_state(case),
        duration_minutes=_compute_duration_minutes(case, chain),
        timeline=timeline,
        worked=worked,
        to_improve=to_improve,
        accuracy=accuracy,
    )


# ------------------------------------------------------------------ #
# Renderer                                                             #
# ------------------------------------------------------------------ #

def render_post_incident_review(review: PostIncidentReview) -> str:
    """Render the review as a markdown-shaped string.

    Paste-into-retrospective-template usage. Stable output shape so
    operators can rely on section headers when piping to other tools.
    """
    lines: list[str] = []

    severity = "P?" if review.severity is None else f"P{review.severity}"
    lines.append(f"# Post-incident review: {review.case_id}")
    lines.append("")
    lines.append(f"- Service: {review.service}")
    lines.append(f"- Severity: {severity}")
    lines.append(f"- State: {review.state}")
    if review.duration_minutes is not None:
        lines.append(f"- Duration: {review.duration_minutes} minutes")

    if review.state == "in_progress":
        lines.append("")
        lines.append("DRAFT — case is still in progress; review will firm up at resolution.")

    lines.append("")
    lines.append("## Timeline")
    if review.timeline:
        for entry in review.timeline:
            confidence = (
                f" (confidence: {entry.confidence:.2f})"
                if entry.confidence is not None
                else ""
            )
            lines.append(
                f"- `{entry.timestamp}` — **{entry.role}** ({entry.actor}): "
                f"{entry.summary}{confidence}"
            )
    else:
        lines.append("- (no events yet)")

    lines.append("")
    lines.append("## What worked")
    if review.worked:
        for line in review.worked:
            lines.append(f"- {line}")
    else:
        lines.append("- (no confirmed outcomes yet)")

    lines.append("")
    lines.append("## What to improve")
    if review.to_improve:
        for line in review.to_improve:
            lines.append(f"- {line}")
    else:
        lines.append("- (no overridden outcomes yet)")

    lines.append("")
    lines.append("## Verdict accuracy")
    if review.accuracy:
        for record in review.accuracy:
            confidence = (
                f"{record.confidence:.2f}" if record.confidence is not None else "—"
            )
            outcome = record.outcome_status or "pending"
            lines.append(
                f"- `{record.verdict_id}` ({record.role}): "
                f"confidence={confidence}, outcome={outcome}"
            )
    else:
        lines.append("- (no verdicts to score)")

    # Overrides section — rendered only when at least one VerdictAccuracy
    # carries mutation-style override attribution (jmy.19). Omit the header
    # entirely when no overrides exist so operators reading the plain-text
    # output don't see a dangling empty section.
    override_records = [a for a in review.accuracy if a.override_by]
    if override_records:
        lines.append("")
        lines.append("## Overrides")
        for record in override_records:
            at_part = f" at {record.override_at}" if record.override_at else ""
            reason_part = (
                f" — {record.override_reasoning}" if record.override_reasoning else ""
            )
            action_part = (
                f"; was {record.override_original_action} → {record.override_action}"
                if record.override_original_action and record.override_action
                else ""
            )
            lines.append(
                f"- {record.role} (`{record.verdict_id}`): "
                f"overridden by {record.override_by}{at_part}{reason_part}{action_part}"
            )

    return "\n".join(lines)
