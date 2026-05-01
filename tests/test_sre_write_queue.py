"""Tests for ``nthlayer_bench.sre.write_queue``."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from nthlayer_common.api_client import APIResult, CoreAPIClient
from nthlayer_common.verdicts import create as verdict_create

from nthlayer_bench.sre.write_queue import DrainResult, PendingNote, WriteQueue


# ------------------------------------------------------------------ #
# Fixture builders                                                     #
# ------------------------------------------------------------------ #

def _verdict(verdict_id_seed: str = ""):
    """Build a minimal valid Verdict for queueing. The ID generation
    in ``verdict_create`` is thread-safe and produces a stable string
    that the queue can replay across drain cycles."""
    v = verdict_create(
        subject={"type": "custom", "ref": "case-1", "summary": f"note {verdict_id_seed}"},
        judgment={"action": "flag", "confidence": 1.0, "reasoning": f"reasoning {verdict_id_seed}"},
        producer={"system": "nthlayer-bench", "instance": "operator"},
        metadata={"custom": {"author": "alice"}},
    )
    v.verdict_type = "operator_note"
    v.parent_ids = ["vrd-anchor"]
    v.service = "fraud-detect"
    return v


def _client_with_submit_responses(*responses: APIResult) -> AsyncMock:
    """Return an AsyncMock client whose ``submit_verdict`` produces the
    given APIResults in order, one per call."""
    client = AsyncMock(spec=CoreAPIClient)
    client.submit_verdict.side_effect = list(responses)
    return client


def _ok() -> APIResult:
    return APIResult(ok=True, status_code=201, data={"id": "x"})


def _conflict() -> APIResult:
    return APIResult(ok=False, status_code=409, data=None, error="duplicate")


def _connection_failed() -> APIResult:
    return APIResult(ok=False, status_code=0, data=None, error="connection_failed")


def _server_error() -> APIResult:
    return APIResult(ok=False, status_code=500, data=None, error="server_error")


# ------------------------------------------------------------------ #
# Enqueue                                                              #
# ------------------------------------------------------------------ #

def test_empty_queue_has_zero_length():
    q = WriteQueue()
    assert len(q) == 0
    assert q.pending() == []


def test_enqueue_appends_pending_note():
    q = WriteQueue()
    q.enqueue(_verdict("a"), "case-1")
    assert len(q) == 1
    snapshot = q.pending()
    assert isinstance(snapshot[0], PendingNote)
    assert snapshot[0].case_id == "case-1"


def test_pending_returns_defensive_copy():
    """Mutating the snapshot returned by ``pending()`` must not affect
    the queue — callers expect to inspect, not mutate."""
    q = WriteQueue()
    q.enqueue(_verdict("a"), "case-1")
    snapshot = q.pending()
    snapshot.clear()
    assert len(q) == 1


# ------------------------------------------------------------------ #
# Drain — empty case                                                   #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_drain_empty_queue_returns_zeros_no_call():
    q = WriteQueue()
    client = AsyncMock(spec=CoreAPIClient)
    result = await q.drain(client)
    assert result == DrainResult()
    client.submit_verdict.assert_not_called()


# ------------------------------------------------------------------ #
# Drain — happy path                                                   #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_drain_successful_submissions_drop_from_queue():
    q = WriteQueue()
    q.enqueue(_verdict("a"), "case-1")
    q.enqueue(_verdict("b"), "case-1")
    client = _client_with_submit_responses(_ok(), _ok())

    result = await q.drain(client)

    assert result.submitted == 2
    assert result.duplicates == 0
    assert result.remaining == 0
    assert len(q) == 0


@pytest.mark.asyncio
async def test_drain_409_drops_from_queue_as_duplicate():
    """409 Conflict means core already has this verdict ID — drop the
    queue entry, don't keep retrying. Stable verdict IDs at enqueue
    time make this contract reliable."""
    q = WriteQueue()
    q.enqueue(_verdict("a"), "case-1")
    client = _client_with_submit_responses(_conflict())

    result = await q.drain(client)

    assert result.submitted == 0
    assert result.duplicates == 1
    assert result.remaining == 0
    assert len(q) == 0


# ------------------------------------------------------------------ #
# Drain — transient errors                                             #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_drain_connection_failed_keeps_queued():
    """status=0 (connection_failed) keeps the entry queued for the
    next drain cycle. This is the steady-state behaviour while core
    is unreachable: queue grows; nothing drains."""
    q = WriteQueue()
    q.enqueue(_verdict("a"), "case-1")
    client = _client_with_submit_responses(_connection_failed())

    result = await q.drain(client)

    assert result.submitted == 0
    assert result.duplicates == 0
    assert result.remaining == 1
    assert len(q) == 1


@pytest.mark.asyncio
async def test_drain_5xx_keeps_queued():
    q = WriteQueue()
    q.enqueue(_verdict("a"), "case-1")
    client = _client_with_submit_responses(_server_error())

    result = await q.drain(client)

    assert result.remaining == 1
    assert len(q) == 1


@pytest.mark.asyncio
async def test_drain_unexpected_exception_keeps_queued_no_raise():
    """A bug in submit_operator_note_verdict (e.g. malformed payload
    raising) must not propagate out of drain — operator's note must
    survive the next cycle. Defensive bare-except in the drain loop."""
    q = WriteQueue()
    q.enqueue(_verdict("a"), "case-1")
    client = AsyncMock(spec=CoreAPIClient)
    client.submit_verdict.side_effect = RuntimeError("unexpected")

    result = await q.drain(client)

    assert result.remaining == 1
    assert len(q) == 1


# ------------------------------------------------------------------ #
# Drain — mixed                                                        #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_drain_mixed_outcomes_partition_correctly():
    """Realistic scenario: one note succeeds, one is a 409 duplicate,
    one hits a transient error. Queue ends with just the transient
    entry; counters reflect each outcome."""
    q = WriteQueue()
    q.enqueue(_verdict("a"), "case-1")  # ok
    q.enqueue(_verdict("b"), "case-1")  # 409
    q.enqueue(_verdict("c"), "case-1")  # connection_failed
    client = _client_with_submit_responses(_ok(), _conflict(), _connection_failed())

    result = await q.drain(client)

    assert result.submitted == 1
    assert result.duplicates == 1
    assert result.remaining == 1
    assert len(q) == 1


@pytest.mark.asyncio
async def test_drain_preserves_fifo_order_of_pending():
    """Failed entries stay in the queue in their original FIFO order
    so the operator's intended write order survives a drain cycle."""
    q = WriteQueue()
    v_a = _verdict("a")
    v_b = _verdict("b")
    v_c = _verdict("c")
    q.enqueue(v_a, "case-1")
    q.enqueue(v_b, "case-1")
    q.enqueue(v_c, "case-1")
    # All connection-failed: nothing succeeds, all stay queued.
    client = _client_with_submit_responses(
        _connection_failed(), _connection_failed(), _connection_failed()
    )

    await q.drain(client)

    pending_ids = [p.verdict.id for p in q.pending()]
    assert pending_ids == [v_a.id, v_b.id, v_c.id]


# ------------------------------------------------------------------ #
# Concurrent drains                                                    #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_concurrent_drain_skipped_via_lock():
    """Skip-if-locked: if the 5s drain tick fires while a previous
    drain is still in flight, the second invocation must not race
    the first on the shared queue. Returns immediately with
    remaining=current-pending-count."""
    q = WriteQueue()
    q.enqueue(_verdict("a"), "case-1")

    gate = asyncio.Event()
    call_count = {"submits": 0}

    async def slow_submit(payload):
        call_count["submits"] += 1
        await gate.wait()
        return _ok()

    client = AsyncMock(spec=CoreAPIClient)
    client.submit_verdict.side_effect = slow_submit

    first = asyncio.create_task(q.drain(client))
    await asyncio.sleep(0)  # let `first` enter and grab the lock
    second_result = await q.drain(client)

    # Second drain saw the lock held → bailed early without calling submit.
    assert call_count["submits"] == 1
    assert second_result.remaining == 1  # snapshot at the moment of skip

    gate.set()
    first_result = await first
    assert first_result.submitted == 1
    assert len(q) == 0
