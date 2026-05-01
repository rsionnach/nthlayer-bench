"""Tests for ``nthlayer_bench.sre.escalation``."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from nthlayer_common.api_client import APIResult, CoreAPIClient

from nthlayer_bench.sre.escalation import (
    ESCALATION_SEVERITIES,
    EscalationEvent,
    EscalationMonitor,
)


# ------------------------------------------------------------------ #
# Fixture builders                                                     #
# ------------------------------------------------------------------ #

def _breach(
    *,
    verdict_id: str,
    severity: str = "high",
    service: str = "fraud-detect",
    summary: str = "reversal rate at 8%",
    created_at: str = "2026-04-30T10:00:00Z",
) -> dict:
    return {
        "id": verdict_id,
        "service": service,
        "verdict_type": "quality_breach",
        "created_at": created_at,
        "subject": {"type": "quality_breach", "ref": service, "summary": summary},
        "judgment": {"action": "flag", "confidence": 0.9, "reasoning": summary},
        "metadata": {"custom": {"severity": severity}},
    }


def _make_client(
    *,
    verdicts: list[dict] | None = None,
    status: int = 200,
) -> AsyncMock:
    client = AsyncMock(spec=CoreAPIClient)
    client.get_verdicts.return_value = APIResult(
        ok=(status == 200),
        status_code=status,
        data=verdicts,
        error=None if status == 200 else f"http_{status}",
    )
    return client


# ------------------------------------------------------------------ #
# Cold start                                                           #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_first_poll_establishes_baseline_returns_no_events():
    """Operator opening the bench shouldn't be spammed with every
    breach in the recent history. The first poll records the IDs as
    baseline and returns empty; subsequent polls fire on deltas."""
    monitor = EscalationMonitor()
    client = _make_client(verdicts=[
        _breach(verdict_id="vrd-pre-1"),
        _breach(verdict_id="vrd-pre-2"),
    ])

    events = await monitor.poll(client)

    assert events == []
    # IDs are recorded so subsequent polls treat them as already seen.
    assert "vrd-pre-1" in monitor._seen_ids
    assert "vrd-pre-2" in monitor._seen_ids


@pytest.mark.asyncio
async def test_first_poll_baseline_with_empty_response():
    """Empty response on first poll still establishes baseline so the
    next poll's results are deltas, not first-time-fired toasts."""
    monitor = EscalationMonitor()
    client = _make_client(verdicts=[])

    events = await monitor.poll(client)

    assert events == []
    assert monitor._baseline_done is True


# ------------------------------------------------------------------ #
# New events after baseline                                            #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_new_event_after_baseline_fires():
    monitor = EscalationMonitor()
    client = _make_client(verdicts=[_breach(verdict_id="vrd-1")])
    await monitor.poll(client)  # baseline

    # New verdict appears.
    client.get_verdicts.return_value = APIResult(
        ok=True, status_code=200,
        data=[_breach(verdict_id="vrd-2"), _breach(verdict_id="vrd-1")],
    )

    events = await monitor.poll(client)

    assert len(events) == 1
    assert events[0].verdict_id == "vrd-2"


@pytest.mark.asyncio
async def test_already_seen_verdicts_do_not_refire():
    monitor = EscalationMonitor()
    client = _make_client(verdicts=[_breach(verdict_id="vrd-1")])
    await monitor.poll(client)
    # Same verdict reappears in next poll (no new events).
    client.get_verdicts.return_value = APIResult(
        ok=True, status_code=200, data=[_breach(verdict_id="vrd-1")],
    )

    events = await monitor.poll(client)

    assert events == []


@pytest.mark.asyncio
async def test_multiple_new_events_returned_newest_first():
    """When several new verdicts land between polls, they're returned
    in newest-first order so consumers downstream can render the most
    pressing escalation at the top of any chronological feed."""
    monitor = EscalationMonitor()
    await monitor.poll(_make_client(verdicts=[]))  # baseline

    client = _make_client(verdicts=[
        _breach(verdict_id="vrd-A", created_at="2026-04-30T10:00:00Z"),
        _breach(verdict_id="vrd-C", created_at="2026-04-30T10:02:00Z"),
        _breach(verdict_id="vrd-B", created_at="2026-04-30T10:01:00Z"),
    ])

    events = await monitor.poll(client)

    assert [e.verdict_id for e in events] == ["vrd-C", "vrd-B", "vrd-A"]


# ------------------------------------------------------------------ #
# Severity filter                                                      #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_low_severity_breaches_dropped():
    """Low-severity breaches stay off the toast queue — they're already
    visible via the case-bench priority signal."""
    monitor = EscalationMonitor()
    await monitor.poll(_make_client(verdicts=[]))  # baseline

    client = _make_client(verdicts=[
        _breach(verdict_id="vrd-low", severity="low"),
        _breach(verdict_id="vrd-high", severity="high"),
        _breach(verdict_id="vrd-critical", severity="critical"),
    ])

    events = await monitor.poll(client)

    assert {e.verdict_id for e in events} == {"vrd-high", "vrd-critical"}


@pytest.mark.asyncio
async def test_missing_severity_dropped():
    monitor = EscalationMonitor()
    await monitor.poll(_make_client(verdicts=[]))

    no_sev = _breach(verdict_id="vrd-1")
    no_sev["metadata"]["custom"] = {}  # severity missing

    client = _make_client(verdicts=[no_sev])
    events = await monitor.poll(client)

    assert events == []


@pytest.mark.asyncio
async def test_non_string_severity_dropped():
    """Defensive: integer or None severity from a deserialisation slip
    must not crash the projection — drop the row, surface nothing."""
    monitor = EscalationMonitor()
    await monitor.poll(_make_client(verdicts=[]))

    bad = _breach(verdict_id="vrd-1")
    bad["metadata"]["custom"]["severity"] = 2  # int, not string

    events = await monitor.poll(_make_client(verdicts=[bad]))
    assert events == []


@pytest.mark.asyncio
async def test_escalation_severities_constant_pinned():
    """Pin the severity taxonomy so a future addition (e.g. medium)
    requires an explicit code change rather than silently flowing
    through to operator toasts."""
    assert ESCALATION_SEVERITIES == frozenset({"high", "critical"})


# ------------------------------------------------------------------ #
# Field projection                                                     #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_event_extracts_service_severity_summary_id_timestamp():
    monitor = EscalationMonitor()
    await monitor.poll(_make_client(verdicts=[]))

    client = _make_client(verdicts=[
        _breach(
            verdict_id="vrd-paid",
            service="payment-api",
            severity="critical",
            summary="checkout cascade",
            created_at="2026-04-30T10:25:00Z",
        )
    ])

    events = await monitor.poll(client)
    e = events[0]
    assert e.verdict_id == "vrd-paid"
    assert e.service == "payment-api"
    assert e.severity == "critical"
    assert e.summary == "checkout cascade"
    assert e.created_at == "2026-04-30T10:25:00Z"
    assert isinstance(e, EscalationEvent)


@pytest.mark.asyncio
async def test_service_falls_back_to_unknown_when_missing_everywhere():
    """Defensive: if both the top-level ``service`` and ``subject.service``
    are missing, the event surfaces with ``service="unknown"`` rather
    than crashing or producing an empty handle. Operator sees
    ``CRITICAL: unknown`` toast — degraded but not a crash."""
    monitor = EscalationMonitor()
    await monitor.poll(_make_client(verdicts=[]))

    breach = _breach(verdict_id="vrd-no-service")
    del breach["service"]
    breach["subject"].pop("service", None)

    events = await monitor.poll(_make_client(verdicts=[breach]))

    assert len(events) == 1
    assert events[0].service == "unknown"


@pytest.mark.asyncio
async def test_summary_falls_back_to_judgment_reasoning():
    """When subject.summary is empty but judgment.reasoning has text,
    surface the reasoning so the toast carries something operator-readable."""
    monitor = EscalationMonitor()
    await monitor.poll(_make_client(verdicts=[]))

    breach = _breach(verdict_id="vrd-1")
    breach["subject"]["summary"] = ""
    breach["judgment"]["reasoning"] = "fallback text"

    events = await monitor.poll(_make_client(verdicts=[breach]))
    assert events[0].summary == "fallback text"


# ------------------------------------------------------------------ #
# Error suppression                                                    #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_connection_failure_returns_empty_no_raise():
    """Toast notifications are best-effort, not load-bearing. A
    connection failure must not crash the toast surface — return empty,
    let the next tick try again."""
    monitor = EscalationMonitor()
    client = _make_client(verdicts=None, status=0)

    events = await monitor.poll(client)

    assert events == []
    # Baseline NOT marked done — a failed first poll shouldn't lock in
    # an empty baseline that suppresses real future events.
    assert monitor._baseline_done is False


@pytest.mark.asyncio
async def test_5xx_error_returns_empty_no_raise():
    monitor = EscalationMonitor()
    client = _make_client(verdicts=None, status=500)
    events = await monitor.poll(client)
    assert events == []


@pytest.mark.asyncio
async def test_error_after_successful_baseline_does_not_clear_seen_ids():
    """Once baseline is established, an intermittent error shouldn't
    cause the next successful poll to refire all the previously-seen
    verdicts as toasts."""
    monitor = EscalationMonitor()
    # Baseline with one verdict.
    await monitor.poll(_make_client(verdicts=[_breach(verdict_id="vrd-baseline")]))
    assert monitor._baseline_done is True

    # Error.
    err_client = _make_client(verdicts=None, status=500)
    await monitor.poll(err_client)

    # Recovery — same baseline verdict still in response.
    recovery = _make_client(verdicts=[_breach(verdict_id="vrd-baseline")])
    events = await monitor.poll(recovery)

    assert events == []  # no refire of the baseline verdict


# ------------------------------------------------------------------ #
# Defensive payload paths                                              #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_non_dict_verdicts_dropped_silently():
    """Same defensive pattern as situation_board: malformed rows
    (string, None) are filtered before projection so AttributeError
    can't escape into the toast surface."""
    monitor = EscalationMonitor()
    await monitor.poll(_make_client(verdicts=[]))

    valid = _breach(verdict_id="vrd-good")
    events = await monitor.poll(
        _make_client(verdicts=[valid, "garbage_string", None])
    )

    assert [e.verdict_id for e in events] == ["vrd-good"]


@pytest.mark.asyncio
async def test_verdict_missing_id_dropped():
    """A verdict without an id can't be deduped against future polls
    (no key to remember it by), so skip it rather than fire the toast
    twice on the next cycle."""
    monitor = EscalationMonitor()
    await monitor.poll(_make_client(verdicts=[]))

    no_id = _breach(verdict_id="placeholder")
    del no_id["id"]

    events = await monitor.poll(_make_client(verdicts=[no_id]))
    assert events == []


@pytest.mark.asyncio
async def test_verdict_with_non_dict_metadata_does_not_crash():
    monitor = EscalationMonitor()
    await monitor.poll(_make_client(verdicts=[]))

    weird = {
        "id": "vrd-weird",
        "verdict_type": "quality_breach",
        "service": "fraud-detect",
        "created_at": "2026-04-30T10:00:00Z",
        "subject": {"type": "quality_breach", "summary": "x"},
        "judgment": {"action": "flag", "confidence": 0.9, "reasoning": "x"},
        "metadata": "not_a_dict",  # malformed
    }

    events = await monitor.poll(_make_client(verdicts=[weird]))
    # Severity unreachable → row dropped from escalation queue.
    assert events == []
