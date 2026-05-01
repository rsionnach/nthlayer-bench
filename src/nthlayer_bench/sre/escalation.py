"""Escalation monitor — drives toast notifications across the bench.

Polls core for new ``quality_breach`` verdicts of high/critical severity
and yields :class:`EscalationEvent` instances to the caller. The bench
app turns each event into a Textual ``app.notify(...)`` toast that the
operator sees regardless of which screen they're currently on.

**Why a class, not a stateless function.** The sister ``sre/`` modules
(``brief``, ``post_incident``, ``case_bench``, ``situation_board``)
expose stateless ``async def fetch_*`` functions because they project
a fresh view on each call. Escalation can't: the whole point is
delta-detection across polls, which requires a per-session memory of
"what we've already seen." :class:`EscalationMonitor` owns that memory
on the app instance. Treat the class as a thin state-holder; almost
all the logic is in :meth:`poll`.

Cold-start semantics: the first successful poll establishes a baseline
of "what's already there" and returns no events, so an operator opening
the bench isn't immediately spammed with every breach in the recent
history. Subsequent polls return only IDs not seen on prior cycles.

Connection failures are swallowed at the monitor boundary — the toast
surface must not crash on transient core unavailability. The other
panels (case bench, situation board, case detail) already render their
own inline error states for the operator's primary view.

Spec: opensrm-81rn.5 (Phase 4 — notification escalation).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

from nthlayer_common.api_client import CoreAPIClient

logger = logging.getLogger(__name__)

# Severity strings produced by measure that warrant a toast. Low-severity
# breaches stay off the operator's radar — the case-bench priority signal
# already covers them; toasts are reserved for "wake up and look".
ESCALATION_SEVERITIES: frozenset[str] = frozenset({"high", "critical"})

DEFAULT_POLL_LIMIT = 20


EscalationSeverity = Literal["high", "critical"]


@dataclass
class EscalationEvent:
    """One notification-worthy event surfaced by the monitor."""

    verdict_id: str
    service: str
    severity: EscalationSeverity  # constrained to ESCALATION_SEVERITIES
    summary: str
    created_at: str               # ISO 8601 from verdict.created_at


@dataclass
class EscalationMonitor:
    """Stateful poller over ``quality_breach`` verdicts.

    Holds a set of seen verdict IDs so polls return only the deltas.
    Designed to live on the app instance; one monitor per app session.
    The first poll's results are recorded as the baseline (no events
    returned) so re-launching the bench after a crash doesn't replay
    the entire breach history as toasts.
    """

    poll_limit: int = DEFAULT_POLL_LIMIT
    _seen_ids: set[str] = field(default_factory=set)
    _baseline_done: bool = False

    async def poll(self, client: CoreAPIClient) -> list[EscalationEvent]:
        """Fetch recent quality_breach verdicts and return new escalations.

        Returns an empty list on the first call (baseline establishment),
        on connection errors, on empty results, and when no recent
        verdicts cross the severity threshold. Failure modes never
        propagate — toast notifications are best-effort, not load-bearing.
        """
        result = await client.get_verdicts(
            verdict_type="quality_breach", limit=self.poll_limit
        )
        if not result.ok:
            # Connection failure or 5xx — skip this cycle and try again
            # next tick. Operator already has inline-error UX on their
            # primary panels; we don't double up via toast spam.
            #
            # IMPORTANT: deliberately do NOT touch _baseline_done here.
            # A failed first poll must leave the monitor in pre-baseline
            # state so the next successful poll establishes baseline
            # cleanly. Otherwise a transient startup error would
            # suppress all real future events.
            logger.debug(
                "escalation_monitor_poll_failed status=%s error=%s",
                result.status_code, result.error,
            )
            return []

        rows = result.data or []
        # Drop non-dict rows defensively (mirrors situation_board's
        # projection-layer guard against malformed payloads).
        verdicts = [v for v in rows if isinstance(v, dict)]

        # Sort newest-first so the most recent escalation surfaces at the
        # top of any chronological consumer of these events.
        verdicts.sort(
            key=lambda v: (v.get("created_at", ""), v.get("id", "")),
            reverse=True,
        )

        new_events: list[EscalationEvent] = []
        seen_in_this_poll: set[str] = set()
        for v in verdicts:
            verdict_id = v.get("id")
            if not isinstance(verdict_id, str) or not verdict_id:
                continue
            seen_in_this_poll.add(verdict_id)
            if verdict_id in self._seen_ids:
                continue
            event = _to_escalation_event(v)
            if event is None:
                continue
            new_events.append(event)

        # Fold all currently-visible IDs into _seen_ids so the next
        # poll's delta is truly "new since now". The set grows
        # unbounded over the app session — acceptable at v1.5 demo
        # scale where one operator sees at most a few hundred breaches
        # per session. If long-running deployments emerge, prune to
        # the last poll_limit*N entries in a follow-up.
        self._seen_ids.update(seen_in_this_poll)

        if not self._baseline_done:
            # First successful poll: record baseline only, don't fire
            # toasts. _seen_ids was just populated; subsequent polls
            # will only see truly-new verdicts.
            self._baseline_done = True
            return []

        return new_events


def _to_escalation_event(verdict: dict) -> EscalationEvent | None:
    """Project a verdict dict into an :class:`EscalationEvent`.

    Returns ``None`` if severity isn't a string in ``ESCALATION_SEVERITIES``
    — low-severity breaches and malformed payloads both fail this filter
    and stay off the operator's toast queue.
    """
    metadata = verdict.get("metadata") or {}
    custom = metadata.get("custom") if isinstance(metadata, dict) else {}
    custom = custom if isinstance(custom, dict) else {}
    severity = custom.get("severity")
    if not isinstance(severity, str) or severity not in ESCALATION_SEVERITIES:
        return None

    subject = verdict.get("subject") or {}
    subject = subject if isinstance(subject, dict) else {}
    judgment = verdict.get("judgment") or {}
    judgment = judgment if isinstance(judgment, dict) else {}
    summary = subject.get("summary") or judgment.get("reasoning", "")

    return EscalationEvent(
        verdict_id=verdict.get("id", ""),
        service=verdict.get("service") or subject.get("service") or "unknown",
        severity=severity,
        summary=summary,
        created_at=verdict.get("created_at", ""),
    )


__all__ = [
    "DEFAULT_POLL_LIMIT",
    "ESCALATION_SEVERITIES",
    "EscalationEvent",
    "EscalationMonitor",
]
