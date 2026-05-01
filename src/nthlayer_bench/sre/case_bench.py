"""Case bench — operator's queue of active cases, grouped by priority.

Polls ``GET /cases?state=pending`` (the active queue) and projects the
HTTP response into ``CaseSummary`` rows. Pure async, no UI deps. Widget
and screen layers above this module own the Textual rendering and the
selection-to-navigation wiring.

Spec reference: ``opensrm-81rn.3`` (Phase 4 / Case bench with priority
grouping). The brief and post-incident review (opensrm-81rn.4.1 /
.4.3) are reachable via this list once a case is selected.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

from nthlayer_common.api_client import CoreAPIClient

from nthlayer_bench.sre.brief import (
    BriefError,
    CoreUnreachableError,
)


# Priority ordering top-to-bottom in the bench. Anything outside this set
# (e.g. an unexpected priority string) is rendered under the "Other" bucket
# at the end so operators don't lose visibility on malformed cases. The
# tuple doubles as a membership test (in is O(n) but n=4) and as the
# canonical display order — single source of truth.
PRIORITY_ORDER: tuple[str, ...] = ("P0", "P1", "P2", "P3")
_OTHER_PRIORITY = "Other"


@dataclass
class CaseSummary:
    """One row in the case bench."""

    case_id: str
    priority: str        # P0/P1/P2/P3 or "Other" for unrecognised values
    service: str
    state: str           # pending / acquired / resolved
    created_at: str      # ISO 8601
    age_minutes: int | None
    briefing: str        # short summary stored on the case at creation


@dataclass
class CaseBenchView:
    """Grouped, sorted view of the active queue.

    ``ordered_priorities`` lists priority buckets in operator-visible order
    (P0 first, "Other" last). ``cases_by_priority`` maps each bucket to its
    cases sorted by created_at ascending (oldest first — they've been
    waiting longest). ``flat`` is the same set in display order so the
    widget can iterate without re-flattening.
    """

    ordered_priorities: list[str] = field(default_factory=list)
    cases_by_priority: dict[str, list[CaseSummary]] = field(default_factory=dict)
    flat: list[CaseSummary] = field(default_factory=list)


class CaseBenchError(BriefError):
    """Raised when the case bench cannot be built. Inherits BriefError so
    a single widget-level catch covers all SRE surfaces."""


# ------------------------------------------------------------------ #
# Public API                                                           #
# ------------------------------------------------------------------ #

async def fetch_case_bench(
    client: CoreAPIClient,
    *,
    state: str | None = "pending",
    limit: int = 100,
    now: datetime | None = None,
) -> CaseBenchView:
    """Fetch the active case queue and project it into a grouped view.

    ``state`` filters the core query (default ``"pending"`` = active
    queue; pass ``None`` to include resolved cases). ``limit`` caps the
    page; v1.5 demo scale is well below this. ``now`` is injectable so
    tests can pin age computation deterministically.
    """
    result = await client.get_cases(state=state, limit=limit)
    if result.ok:
        cases_data = result.data or []
    elif result.status_code == 0:
        raise CoreUnreachableError(result.detail or {"error": result.error})
    else:
        raise CaseBenchError(
            f"get_cases failed: {result.error} (status={result.status_code})"
        )

    reference = now or datetime.now(timezone.utc)
    summaries = [_to_summary(case, reference) for case in cases_data]

    return _group_and_sort(summaries)


def _to_summary(case: dict, now: datetime) -> CaseSummary:
    raw_priority = case.get("priority", _OTHER_PRIORITY)
    priority = raw_priority if raw_priority in PRIORITY_ORDER else _OTHER_PRIORITY
    return CaseSummary(
        case_id=case.get("id", ""),
        priority=priority,
        service=case.get("service") or "unknown",
        state=case.get("state", "pending"),
        created_at=case.get("created_at", ""),
        age_minutes=_age_minutes(case.get("created_at"), now),
        briefing=case.get("briefing") or "",
    )


def _age_minutes(created_at: str | None, now: datetime) -> int | None:
    """Compute case age in minutes, fail-closed to None on malformed input.

    Mirrors post_incident.py's TypeError-safe duration computation —
    migration-era payloads can mix tz-naive and tz-aware ISO strings.
    """
    if not created_at:
        return None
    try:
        start = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        delta = now - start
    except (ValueError, AttributeError, TypeError):
        return None
    return max(0, int(delta.total_seconds() // 60))


def _group_and_sort(summaries: Iterable[CaseSummary]) -> CaseBenchView:
    cases_by_priority: dict[str, list[CaseSummary]] = {}
    for summary in summaries:
        cases_by_priority.setdefault(summary.priority, []).append(summary)

    # Sort each priority bucket by created_at ascending (oldest first —
    # cases that have been waiting longest get top of bucket).
    for bucket in cases_by_priority.values():
        bucket.sort(key=lambda c: (c.created_at, c.case_id))

    # Build operator-visible priority order: known priorities in canonical
    # order, then any other buckets at the end (alphabetical for stability).
    ordered: list[str] = [p for p in PRIORITY_ORDER if p in cases_by_priority]
    extras = sorted(p for p in cases_by_priority if p not in PRIORITY_ORDER)
    ordered.extend(extras)

    flat: list[CaseSummary] = []
    for priority in ordered:
        flat.extend(cases_by_priority[priority])

    return CaseBenchView(
        ordered_priorities=ordered,
        cases_by_priority=cases_by_priority,
        flat=flat,
    )


def render_case_bench(view: CaseBenchView) -> str:
    """Plain-text rendering — used by tests and any future text-only
    consumer (e.g. paste into a status update)."""
    if not view.flat:
        return "No active cases."

    blocks: list[str] = []
    for priority in view.ordered_priorities:
        bucket = view.cases_by_priority[priority]
        block_lines: list[str] = [f"## {priority} ({len(bucket)})"]
        for case in bucket:
            age = (
                f"{case.age_minutes}m" if case.age_minutes is not None else "—"
            )
            briefing = f" — {case.briefing}" if case.briefing else ""
            block_lines.append(
                f"- {case.case_id}  {case.service}  [{case.state}]  "
                f"age={age}{briefing}"
            )
        blocks.append("\n".join(block_lines))
    return "\n\n".join(blocks)
