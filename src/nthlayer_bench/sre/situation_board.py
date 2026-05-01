"""Situation board — system-state-at-a-glance dashboard.

Composes three live signals from core into a single operator-readable
view:

- **Portfolio status** — latest ``portfolio_status`` assessment from
  observe (per-service health roll-up: HEALTHY / WARNING / CRITICAL /
  EXHAUSTED counts).
- **Recent breaches** — most recent ``quality_breach`` verdicts from
  measure (the last few signals that flipped a service's state).
- **Active queue size** — case-count breakdown by priority, reusing the
  case-bench fetch.

Pure async, no UI deps. Widget and screen layers above own the Textual
rendering. Spec: opensrm-81rn.2 (Phase 4 — situation board).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from nthlayer_common.api_client import CoreAPIClient

from nthlayer_bench.sre.brief import (
    BriefError,
    CoreUnreachableError,
)
from nthlayer_bench.sre.case_bench import (
    CaseBenchView,
    fetch_case_bench,
)


@dataclass
class PortfolioSnapshot:
    """Per-service health roll-up from the latest portfolio assessment."""

    total_services: int
    healthy: int
    warning: int
    critical: int
    exhausted: int
    captured_at: str  # ISO 8601 from the assessment


@dataclass
class BreachEvent:
    """One row in the recent-breaches feed."""

    verdict_id: str
    service: str
    summary: str
    created_at: str
    severity: str | None  # "low" | "high" | "critical" — set by measure


@dataclass
class SituationBoardView:
    """Composite view rendered by the widget. Each section may be empty
    if its source has produced nothing yet (cold-start operator opens
    bench before workers have run a cycle)."""

    portfolio: PortfolioSnapshot | None = None
    recent_breaches: list[BreachEvent] = field(default_factory=list)
    queue: CaseBenchView = field(default_factory=CaseBenchView)


class SituationBoardError(BriefError):
    """Raised when the situation board cannot be built. Inherits
    BriefError so widgets catch all SRE error paths through one filter."""


# ------------------------------------------------------------------ #
# Public API                                                           #
# ------------------------------------------------------------------ #

async def fetch_situation_board(
    client: CoreAPIClient,
    *,
    breach_limit: int = 10,
) -> SituationBoardView:
    """Compose the situation-board view from three live core endpoints.

    Fail-closed semantics: a connection failure on any of the three
    fetches raises :class:`CoreUnreachableError` so the widget shows a
    single inline error rather than a half-rendered dashboard. Other
    non-2xx responses produce :class:`SituationBoardError`.

    Fetches run in parallel via :func:`asyncio.gather` — three
    independent core round-trips per cycle, no reason to serialise them.
    The first exception propagates directly (no ``ExceptionGroup``
    wrapping) so the widget's ``except CoreUnreachableError`` /
    ``except SituationBoardError`` clauses catch the right error type.
    Sibling tasks continue to completion and their results are
    discarded, which is fine at v1.5 demo scale.
    """
    portfolio, breaches, queue = await asyncio.gather(
        _fetch_portfolio(client),
        _fetch_recent_breaches(client, limit=breach_limit),
        fetch_case_bench(client),
    )
    return SituationBoardView(
        portfolio=portfolio,
        recent_breaches=breaches,
        queue=queue,
    )


async def _fetch_portfolio(client: CoreAPIClient) -> PortfolioSnapshot | None:
    result = await client.get_assessments(kind="portfolio_status", limit=1)
    if result.status_code == 0:
        raise CoreUnreachableError(result.detail or {"error": result.error})
    if not result.ok:
        raise SituationBoardError(
            f"get_assessments(portfolio_status) failed: {result.error} "
            f"(status={result.status_code})"
        )
    rows = result.data or []
    if not rows:
        # Worker hasn't produced a portfolio_status assessment yet (cold
        # start, or core wiped). Caller renders "Waiting for portfolio
        # data." in that case.
        return None
    return _project_portfolio(rows[0])


def _project_portfolio(assessment: object) -> PortfolioSnapshot | None:
    # Defensive: a non-dict row (deserialisation slip, schema drift)
    # would crash the panel via AttributeError, escaping the inline-error
    # envelope. Treat as missing and let the caller render the "Waiting
    # for portfolio data." placeholder.
    if not isinstance(assessment, dict):
        return None
    raw_data = assessment.get("data")
    data = raw_data if isinstance(raw_data, dict) else {}
    total = data.get("total_services")
    if not isinstance(total, int):
        # Malformed payload — don't fabricate counts. Caller sees the
        # "Waiting for portfolio data." placeholder.
        return None
    return PortfolioSnapshot(
        total_services=total,
        healthy=_int_or_zero(data, "healthy_count"),
        warning=_int_or_zero(data, "warning_count"),
        critical=_int_or_zero(data, "critical_count"),
        exhausted=_int_or_zero(data, "exhausted_count"),
        captured_at=assessment.get("created_at", "") or "",
    )


def _int_or_zero(data: dict, key: str) -> int:
    value = data.get(key)
    return value if isinstance(value, int) else 0


async def _fetch_recent_breaches(
    client: CoreAPIClient, *, limit: int
) -> list[BreachEvent]:
    result = await client.get_verdicts(verdict_type="quality_breach", limit=limit)
    if result.status_code == 0:
        raise CoreUnreachableError(result.detail or {"error": result.error})
    if not result.ok:
        raise SituationBoardError(
            f"get_verdicts(quality_breach) failed: {result.error} "
            f"(status={result.status_code})"
        )
    rows = result.data or []
    # Drop non-dict rows defensively before sort/projection.
    valid_rows = [v for v in rows if isinstance(v, dict)]
    # Sort desc by (created_at, id) — newest first.
    valid_rows.sort(
        key=lambda v: (v.get("created_at", ""), v.get("id", "")), reverse=True
    )
    return [_to_breach_event(v) for v in valid_rows]


def _safe_dict(value: object) -> dict:
    """Return ``value`` if it's a dict, otherwise ``{}``. Used to keep
    nested-field access in :func:`_to_breach_event` from raising on
    malformed payloads (subject/judgment/metadata as a non-dict)."""
    return value if isinstance(value, dict) else {}


def _to_breach_event(verdict: dict) -> BreachEvent:
    subject = _safe_dict(verdict.get("subject"))
    judgment = _safe_dict(verdict.get("judgment"))
    metadata = _safe_dict(verdict.get("metadata"))
    custom = _safe_dict(metadata.get("custom"))
    summary = subject.get("summary") or judgment.get("reasoning", "")
    severity = custom.get("severity")
    return BreachEvent(
        verdict_id=verdict.get("id", ""),
        service=verdict.get("service") or subject.get("service") or "unknown",
        summary=summary,
        created_at=verdict.get("created_at", ""),
        severity=severity if isinstance(severity, str) else None,
    )


# ------------------------------------------------------------------ #
# Renderer                                                             #
# ------------------------------------------------------------------ #

def render_situation_board(view: SituationBoardView) -> str:
    """Plain-text rendering — used by tests and any future text-only
    consumer (e.g. shift-handover paste, status-channel snapshot)."""
    sections: list[str] = []

    sections.append("# Situation board")

    # Portfolio
    if view.portfolio is not None:
        p = view.portfolio
        sections.append(
            "\n".join(
                [
                    "## Portfolio",
                    f"- Services: {p.total_services}",
                    f"- Healthy:   {p.healthy}",
                    f"- Warning:   {p.warning}",
                    f"- Critical:  {p.critical}",
                    f"- Exhausted: {p.exhausted}",
                    f"- Captured:  {p.captured_at}",
                ]
            )
        )
    else:
        sections.append("\n".join(["## Portfolio", "- Waiting for portfolio data."]))

    # Recent breaches
    if view.recent_breaches:
        breach_lines = ["## Recent quality breaches"]
        for event in view.recent_breaches:
            severity = f"[{event.severity}] " if event.severity else ""
            breach_lines.append(
                f"- `{event.created_at}` {severity}{event.service}: {event.summary}"
            )
        sections.append("\n".join(breach_lines))
    else:
        sections.append(
            "\n".join(["## Recent quality breaches", "- No recent quality breaches."])
        )

    # Queue
    if view.queue.flat:
        queue_lines = [f"## Active queue ({len(view.queue.flat)})"]
        for priority in view.queue.ordered_priorities:
            count = len(view.queue.cases_by_priority[priority])
            queue_lines.append(f"- {priority}: {count}")
        sections.append("\n".join(queue_lines))
    else:
        sections.append("\n".join(["## Active queue", "- No active cases."]))

    return "\n\n".join(sections)
