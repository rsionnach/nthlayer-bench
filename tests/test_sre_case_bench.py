"""Tests for ``nthlayer_bench.sre.case_bench``.

Logic-module tests use ``AsyncMock`` for ``CoreAPIClient`` and inject
``APIResult`` payloads. The bench operates on JSON dicts (HTTP response
shape).
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from nthlayer_common.api_client import APIResult, CoreAPIClient

from nthlayer_bench.sre.case_bench import (
    CaseBenchError,
    CoreUnreachableError,
    PRIORITY_ORDER,
    fetch_case_bench,
    render_case_bench,
)


# ------------------------------------------------------------------ #
# Fixtures                                                             #
# ------------------------------------------------------------------ #

def _case(
    case_id: str,
    *,
    priority: str = "P1",
    service: str = "fraud-detect",
    state: str = "pending",
    created_at: str = "2026-04-30T10:00:00Z",
    briefing: str = "",
) -> dict:
    return {
        "id": case_id,
        "priority": priority,
        "service": service,
        "state": state,
        "created_at": created_at,
        "briefing": briefing,
        "underlying_verdict": f"vrd-{case_id}",
    }


def _make_client(
    *,
    cases: list[dict] | None = None,
    status: int = 200,
    error: str | None = None,
) -> AsyncMock:
    client = AsyncMock(spec=CoreAPIClient)
    client.get_cases.return_value = APIResult(
        ok=(status == 200),
        status_code=status,
        data=cases,
        error=error or (None if status == 200 else f"http_{status}"),
    )
    return client


_NOW = datetime(2026, 4, 30, 10, 30, 0, tzinfo=timezone.utc)


# ------------------------------------------------------------------ #
# Grouping                                                             #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_cases_are_grouped_by_priority_in_canonical_order():
    """Priorities appear top-to-bottom in PRIORITY_ORDER (P0 first),
    not in arbitrary dict-iteration order."""
    cases = [
        _case("c-p3", priority="P3"),
        _case("c-p1", priority="P1"),
        _case("c-p0", priority="P0"),
        _case("c-p2", priority="P2"),
    ]
    client = _make_client(cases=cases)

    view = await fetch_case_bench(client, now=_NOW)

    assert view.ordered_priorities == ["P0", "P1", "P2", "P3"]


@pytest.mark.asyncio
async def test_unrecognised_priority_lands_in_other_bucket():
    """A case with a malformed priority value (or one we don't know
    about) lands in the 'Other' bucket at the end so operators don't
    lose visibility on it."""
    cases = [
        _case("c-known", priority="P1"),
        _case("c-unknown", priority="urgent"),
    ]
    client = _make_client(cases=cases)

    view = await fetch_case_bench(client, now=_NOW)

    assert "Other" in view.cases_by_priority
    assert view.ordered_priorities[-1] == "Other"
    other_bucket = view.cases_by_priority["Other"]
    assert other_bucket[0].case_id == "c-unknown"
    assert other_bucket[0].priority == "Other"


@pytest.mark.asyncio
async def test_within_priority_bucket_oldest_first():
    """Within a bucket, oldest case is at the top — the one that's been
    waiting longest deserves the first look."""
    cases = [
        _case("c-newer", priority="P1", created_at="2026-04-30T10:20:00Z"),
        _case("c-older", priority="P1", created_at="2026-04-30T10:00:00Z"),
        _case("c-mid",   priority="P1", created_at="2026-04-30T10:10:00Z"),
    ]
    client = _make_client(cases=cases)

    view = await fetch_case_bench(client, now=_NOW)

    bucket = view.cases_by_priority["P1"]
    assert [c.case_id for c in bucket] == ["c-older", "c-mid", "c-newer"]


@pytest.mark.asyncio
async def test_empty_response_yields_empty_view():
    client = _make_client(cases=[])
    view = await fetch_case_bench(client, now=_NOW)
    assert view.flat == []
    assert view.ordered_priorities == []


@pytest.mark.asyncio
async def test_flat_iteration_order_matches_priority_then_age():
    """The `flat` field is the same set of cases in display order so
    the widget can iterate without re-flattening."""
    cases = [
        _case("c-p1-newer", priority="P1", created_at="2026-04-30T10:10:00Z"),
        _case("c-p0",       priority="P0", created_at="2026-04-30T10:00:00Z"),
        _case("c-p1-older", priority="P1", created_at="2026-04-30T09:50:00Z"),
    ]
    client = _make_client(cases=cases)

    view = await fetch_case_bench(client, now=_NOW)

    assert [c.case_id for c in view.flat] == ["c-p0", "c-p1-older", "c-p1-newer"]


# ------------------------------------------------------------------ #
# Field projection                                                     #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_summary_carries_service_state_and_briefing():
    cases = [
        _case(
            "c-1",
            priority="P0",
            service="payment-api",
            state="acquired",
            briefing="checkout failure cascade",
        )
    ]
    client = _make_client(cases=cases)

    view = await fetch_case_bench(client, now=_NOW)
    summary = view.flat[0]

    assert summary.service == "payment-api"
    assert summary.state == "acquired"
    assert summary.briefing == "checkout failure cascade"


@pytest.mark.asyncio
async def test_missing_service_falls_back_to_unknown():
    cases = [{"id": "c-1", "priority": "P1", "state": "pending",
              "created_at": "2026-04-30T10:00:00Z", "underlying_verdict": "vrd-1"}]
    client = _make_client(cases=cases)

    view = await fetch_case_bench(client, now=_NOW)

    assert view.flat[0].service == "unknown"


@pytest.mark.asyncio
async def test_age_minutes_computed_from_now():
    cases = [_case("c-1", created_at="2026-04-30T10:00:00Z")]
    client = _make_client(cases=cases)

    view = await fetch_case_bench(client, now=_NOW)

    assert view.flat[0].age_minutes == 30


@pytest.mark.asyncio
async def test_age_minutes_none_on_malformed_created_at():
    """Mirrors post_incident's TypeError-safe duration: malformed ISO
    strings fail closed to None rather than crashing."""
    cases = [_case("c-1", created_at="not-a-timestamp")]
    client = _make_client(cases=cases)

    view = await fetch_case_bench(client, now=_NOW)

    assert view.flat[0].age_minutes is None


@pytest.mark.asyncio
async def test_age_minutes_clamps_at_zero_for_future_created_at():
    """Clock skew can put created_at in the future relative to now;
    clamp to 0 rather than producing a negative age."""
    cases = [_case("c-1", created_at="2026-04-30T11:00:00Z")]  # 30 min ahead
    client = _make_client(cases=cases)

    view = await fetch_case_bench(client, now=_NOW)

    assert view.flat[0].age_minutes == 0


# ------------------------------------------------------------------ #
# State filtering                                                      #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_default_state_filter_is_pending():
    """Default behavior: query the active queue. Resolved cases live in
    history, not in the bench."""
    client = _make_client(cases=[])
    await fetch_case_bench(client, now=_NOW)
    client.get_cases.assert_awaited_with(state="pending", limit=100)


@pytest.mark.asyncio
async def test_state_none_omits_filter():
    """Passing state=None lets the operator see all cases including
    resolved — useful for debugging or audits."""
    client = _make_client(cases=[])
    await fetch_case_bench(client, state=None, now=_NOW)
    client.get_cases.assert_awaited_with(state=None, limit=100)


# ------------------------------------------------------------------ #
# Error handling                                                       #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_connection_failed_raises_core_unreachable():
    client = _make_client(cases=None, status=0, error="connection_failed")
    with pytest.raises(CoreUnreachableError):
        await fetch_case_bench(client, now=_NOW)


@pytest.mark.asyncio
async def test_other_non_2xx_raises_case_bench_error():
    client = _make_client(cases=None, status=500, error="server_error")
    with pytest.raises(CaseBenchError):
        await fetch_case_bench(client, now=_NOW)


# ------------------------------------------------------------------ #
# Renderer                                                             #
# ------------------------------------------------------------------ #

class TestRenderCaseBench:
    @pytest.mark.asyncio
    async def test_empty_view_renders_no_active_cases(self):
        client = _make_client(cases=[])
        view = await fetch_case_bench(client, now=_NOW)
        assert render_case_bench(view) == "No active cases."

    @pytest.mark.asyncio
    async def test_renderer_includes_priority_section_headers(self):
        cases = [
            _case("c-p0", priority="P0"),
            _case("c-p1", priority="P1"),
        ]
        client = _make_client(cases=cases)
        view = await fetch_case_bench(client, now=_NOW)
        text = render_case_bench(view)
        assert "## P0 (1)" in text
        assert "## P1 (1)" in text

    @pytest.mark.asyncio
    async def test_renderer_includes_case_id_service_state_age(self):
        cases = [_case("c-XYZ", priority="P1", service="fraud-detect", state="acquired")]
        client = _make_client(cases=cases)
        view = await fetch_case_bench(client, now=_NOW)
        text = render_case_bench(view)
        assert "c-XYZ" in text
        assert "fraud-detect" in text
        assert "[acquired]" in text
        assert "age=30m" in text

    @pytest.mark.asyncio
    async def test_renderer_includes_briefing_when_present(self):
        cases = [_case("c-1", briefing="reversal rate breach")]
        client = _make_client(cases=cases)
        view = await fetch_case_bench(client, now=_NOW)
        text = render_case_bench(view)
        assert "reversal rate breach" in text


# ------------------------------------------------------------------ #
# Constants pinning                                                    #
# ------------------------------------------------------------------ #

def test_priority_order_is_p0_to_p3():
    """Pin the priority taxonomy so a future addition (P-1, P-, etc.)
    requires an explicit code change."""
    assert PRIORITY_ORDER == ("P0", "P1", "P2", "P3")


# ------------------------------------------------------------------ #
# R5 Pass 3 defensive coverage                                         #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_non_string_priority_buckets_to_other():
    """A core payload with `priority: 42` (int) shouldn't crash —
    `42 in PRIORITY_ORDER` is False, so the case lands in the 'Other'
    bucket. Pin the contract so a future PRIORITY_ORDER change can't
    silently let non-string priorities through."""
    cases = [
        {"id": "c-int", "priority": 42, "service": "x", "state": "pending",
         "created_at": "2026-04-30T10:00:00Z", "underlying_verdict": "v"},
        {"id": "c-none", "priority": None, "service": "x", "state": "pending",
         "created_at": "2026-04-30T10:00:00Z", "underlying_verdict": "v"},
    ]
    client = _make_client(cases=cases)

    view = await fetch_case_bench(client, now=_NOW)

    assert "Other" in view.cases_by_priority
    assert len(view.cases_by_priority["Other"]) == 2


@pytest.mark.asyncio
async def test_blank_case_id_passes_through_to_summary():
    """A case missing `id` reaches the projection layer with case_id="".
    The widget tolerates a single such row; the logic layer doesn't
    silently drop it (silent drop would mask a producer bug)."""
    cases = [{"priority": "P1", "service": "x", "state": "pending",
              "created_at": "2026-04-30T10:00:00Z", "underlying_verdict": "v"}]
    client = _make_client(cases=cases)

    view = await fetch_case_bench(client, now=_NOW)

    assert len(view.flat) == 1
    assert view.flat[0].case_id == ""
