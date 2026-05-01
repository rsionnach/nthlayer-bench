"""Write queue — defer operator-note submissions across core outages.

The reasoning-capture panel (Bead 7) submits operator notes
synchronously: a connection failure surfaces an inline error and keeps
the operator's text in the input for manual retry. That's adequate for
brief network blips but loses ergonomics during a longer outage —
the operator types a note, sees an error, and has to remember to
re-submit later.

This module backs a write queue on :class:`BenchApp`:

- The panel calls :meth:`WriteQueue.enqueue` with a pre-built
  :class:`Verdict` (constructed from the panel's cached case data) when
  it sees :class:`CoreUnreachableError` from a synchronous submit.
- An app-level drain timer calls :meth:`WriteQueue.drain` every 5s.
  Each pending verdict is replayed; success and 409 (already accepted)
  both drop the entry, transient errors keep it queued.
- Verdicts are built once at enqueue time, so the queued ID is stable
  across replays — letting core return 409 cleanly on the duplicate
  rather than a queued note appearing twice in the audit trail.

In-memory only: the queue does not survive an app restart. Persistent
queueing is deferred to v2 (would require local storage + replay-on-
startup that's out of scope for the Tier 3 TUI).

Spec: opensrm-81rn.1 acceptance gap — write queue with 409 conflict
detection.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from nthlayer_common.api_client import CoreAPIClient
from nthlayer_common.verdicts import Verdict

from nthlayer_bench.sre.reasoning_capture import submit_operator_note_verdict

logger = logging.getLogger(__name__)


@dataclass
class PendingNote:
    """One queued operator note awaiting submission."""

    verdict: Verdict
    case_id: str
    # Captured for v2 persistence/audit when the queue gains a
    # disk-backed store. Unused in v1.5 — flagged here so a future
    # reader doesn't drop it as dead weight.
    enqueued_at: datetime


@dataclass
class DrainResult:
    """Outcome of one drain cycle, for app-level UX (toast counts,
    status-line display)."""

    submitted: int = 0       # succeeded outright (201 Created)
    duplicates: int = 0      # core returned 409, dropped from queue
    remaining: int = 0       # still queued after this cycle


class WriteQueue:
    """In-memory FIFO of pending operator-note submissions."""

    def __init__(self) -> None:
        self._pending: list[PendingNote] = []
        # Lock against concurrent drains (the 5s app-level interval
        # could fire while a previous drain is still in flight on a
        # slow core). Same skip-if-locked pattern as the bench panels.
        self._drain_lock = asyncio.Lock()

    def __len__(self) -> int:
        return len(self._pending)

    def enqueue(self, verdict: Verdict, case_id: str) -> None:
        """Append a pre-built verdict to the queue. Caller has already
        constructed the verdict (giving it a stable ID), so a queued
        retry submits the same ID core saw on any prior attempt."""
        self._pending.append(
            PendingNote(
                verdict=verdict,
                case_id=case_id,
                enqueued_at=datetime.now(timezone.utc),
            )
        )

    def pending(self) -> list[PendingNote]:
        """Snapshot of currently queued items. Returned list is a
        defensive copy — mutating it doesn't affect the queue."""
        return list(self._pending)

    async def drain(self, client: CoreAPIClient) -> DrainResult:
        """Replay all pending submissions. Drops succeeded and
        409-duplicate entries from the queue; keeps transient failures
        for the next drain cycle.

        Skip-if-locked: a concurrent drain (slow core + 5s tick)
        returns an immediate empty result rather than racing the
        original drain on the same queue.
        """
        if self._drain_lock.locked():
            return DrainResult(remaining=len(self._pending))

        async with self._drain_lock:
            if not self._pending:
                return DrainResult()

            submitted = 0
            duplicates = 0
            still_pending: list[PendingNote] = []

            for item in self._pending:
                try:
                    result = await submit_operator_note_verdict(client, item.verdict)
                except Exception as exc:  # noqa: BLE001 — never let drain crash
                    # Defensive: any unexpected exception keeps the item
                    # queued so it can be replayed on the next cycle. The
                    # operator's note isn't lost.
                    logger.warning(
                        "write_queue_drain_unexpected_exception verdict_id=%s exc=%s",
                        item.verdict.id, exc,
                    )
                    still_pending.append(item)
                    continue

                if result.ok:
                    submitted += 1
                    continue
                if result.status_code == 409:
                    # Already accepted on a prior attempt — drop from
                    # the queue cleanly. This is the load-bearing case
                    # for stable verdict IDs at enqueue time.
                    duplicates += 1
                    continue
                # Anything else (status=0 / 5xx / 4xx other than 409):
                # keep queued for the next drain. v1.5 doesn't try to
                # distinguish permanent 4xx from transient 5xx — the
                # operator can manually clear the input if a sticky
                # error builds up.
                still_pending.append(item)

            self._pending = still_pending
            return DrainResult(
                submitted=submitted,
                duplicates=duplicates,
                remaining=len(self._pending),
            )


__all__ = ["DrainResult", "PendingNote", "WriteQueue"]
