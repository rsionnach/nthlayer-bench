"""Tests for ``nthlayer_bench.sre.situation_board``."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from nthlayer_common.api_client import APIResult, CoreAPIClient

from nthlayer_bench.sre.case_bench import CaseBenchView, CaseSummary
from nthlayer_bench.sre.situation_board import (
    BreachEvent,
    CoreUnreachableError,
    PortfolioSnapshot,
    SituationBoardError,
    SituationBoardView,
    fetch_situation_board,
    render_situation_board,
)


# ------------------------------------------------------------------ #
# Fixture builders                                                     #
# ------------------------------------------------------------------ #

def _portfolio_assessment(**counts: int) -> dict:
    data = {
        "total_services": counts.get("total_services", 0),
        "healthy_count": counts.get("healthy_count", 0),
        "warning_count": counts.get("warning_count", 0),
        "critical_count": counts.get("critical_count", 0),
        "exhausted_count": counts.get("exhausted_count", 0),
    }
    return {
        "id": "asm-portfolio-001",
        "kind": "portfolio_status",
        "service": "__portfolio__",
        "created_at": "2026-04-30T10:30:00Z",
        "data": data,
    }


def _breach_verdict(
    *,
    verdict_id: str = "vrd-breach-001",
    service: str = "fraud-detect",
    summary: str = "reversal rate at 8%, target 1.5%",
    created_at: str = "2026-04-30T10:25:00Z",
    severity: str | None = "high",
) -> dict:
    custom: dict = {}
    if severity is not None:
        custom["severity"] = severity
    return {
        "id": verdict_id,
        "service": service,
        "verdict_type": "quality_breach",
        "created_at": created_at,
        "subject": {"type": "quality_breach", "ref": service, "summary": summary},
        "judgment": {"action": "flag", "confidence": 0.9, "reasoning": summary},
        "metadata": {"custom": custom},
    }


def _make_client(
    *,
    portfolio_rows: list[dict] | None = None,
    portfolio_status: int = 200,
    breach_rows: list[dict] | None = None,
    breach_status: int = 200,
    cases: list[dict] | None = None,
    cases_status: int = 200,
) -> AsyncMock:
    client = AsyncMock(spec=CoreAPIClient)
    client.get_assessments.return_value = APIResult(
        ok=(portfolio_status == 200),
        status_code=portfolio_status,
        data=portfolio_rows,
        error=None if portfolio_status == 200 else f"http_{portfolio_status}",
    )
    client.get_verdicts.return_value = APIResult(
        ok=(breach_status == 200),
        status_code=breach_status,
        data=breach_rows,
        error=None if breach_status == 200 else f"http_{breach_status}",
    )
    client.get_cases.return_value = APIResult(
        ok=(cases_status == 200),
        status_code=cases_status,
        data=cases,
        error=None if cases_status == 200 else f"http_{cases_status}",
    )
    return client


# ------------------------------------------------------------------ #
# Portfolio projection                                                 #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_latest_portfolio_assessment_projected_into_snapshot():
    portfolio = _portfolio_assessment(
        total_services=5,
        healthy_count=3,
        warning_count=1,
        critical_count=1,
        exhausted_count=0,
    )
    client = _make_client(portfolio_rows=[portfolio], breach_rows=[], cases=[])

    view = await fetch_situation_board(client)

    assert view.portfolio is not None
    assert view.portfolio.total_services == 5
    assert view.portfolio.healthy == 3
    assert view.portfolio.warning == 1
    assert view.portfolio.critical == 1
    assert view.portfolio.exhausted == 0
    assert view.portfolio.captured_at == "2026-04-30T10:30:00Z"


@pytest.mark.asyncio
async def test_no_portfolio_assessment_yields_none_snapshot():
    """Cold start (worker hasn't run a cycle yet) → portfolio is None.
    Caller renders 'Waiting for portfolio data.' rather than fabricating
    counts from nothing."""
    client = _make_client(portfolio_rows=[], breach_rows=[], cases=[])
    view = await fetch_situation_board(client)
    assert view.portfolio is None


@pytest.mark.asyncio
async def test_malformed_portfolio_payload_yields_none_snapshot():
    """If total_services isn't an int (deserialisation slip, schema
    drift), don't fabricate — fall back to None and let the renderer
    show the placeholder."""
    bad = {"id": "asm-bad", "kind": "portfolio_status", "created_at": "x",
           "data": {"total_services": "five"}}
    client = _make_client(portfolio_rows=[bad], breach_rows=[], cases=[])
    view = await fetch_situation_board(client)
    assert view.portfolio is None


@pytest.mark.asyncio
async def test_missing_count_fields_default_to_zero():
    """A portfolio assessment with total_services but missing per-status
    counts (older schema, partial payload) defaults the missing counts
    to 0 — total still surfaces."""
    minimal = {
        "id": "asm-min",
        "kind": "portfolio_status",
        "created_at": "2026-04-30T10:30:00Z",
        "data": {"total_services": 3},  # only total, no per-status counts
    }
    client = _make_client(portfolio_rows=[minimal], breach_rows=[], cases=[])
    view = await fetch_situation_board(client)
    assert view.portfolio is not None
    assert view.portfolio.total_services == 3
    assert view.portfolio.healthy == 0
    assert view.portfolio.warning == 0
    assert view.portfolio.critical == 0
    assert view.portfolio.exhausted == 0


# ------------------------------------------------------------------ #
# Breach feed                                                          #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_recent_breaches_sorted_newest_first():
    older = _breach_verdict(
        verdict_id="vrd-old", created_at="2026-04-30T10:00:00Z"
    )
    newer = _breach_verdict(
        verdict_id="vrd-new", created_at="2026-04-30T10:25:00Z"
    )
    client = _make_client(
        portfolio_rows=[],
        breach_rows=[older, newer],  # arrive in arbitrary order
        cases=[],
    )

    view = await fetch_situation_board(client)

    assert [b.verdict_id for b in view.recent_breaches] == ["vrd-new", "vrd-old"]


@pytest.mark.asyncio
async def test_breach_event_extracts_service_summary_severity():
    breach = _breach_verdict(
        service="payment-api",
        summary="checkout cascade",
        severity="critical",
    )
    client = _make_client(portfolio_rows=[], breach_rows=[breach], cases=[])

    view = await fetch_situation_board(client)
    event = view.recent_breaches[0]

    assert event.service == "payment-api"
    assert event.summary == "checkout cascade"
    assert event.severity == "critical"


@pytest.mark.asyncio
async def test_breach_event_severity_none_when_missing():
    breach = _breach_verdict(severity=None)
    client = _make_client(portfolio_rows=[], breach_rows=[breach], cases=[])

    view = await fetch_situation_board(client)

    assert view.recent_breaches[0].severity is None


@pytest.mark.asyncio
async def test_breach_event_severity_none_when_non_string():
    """Defensive: int severity (deserialisation slip) → None rather than
    forwarding the wrong type to renderers."""
    breach = _breach_verdict()
    breach["metadata"]["custom"]["severity"] = 2  # int
    client = _make_client(portfolio_rows=[], breach_rows=[breach], cases=[])

    view = await fetch_situation_board(client)

    assert view.recent_breaches[0].severity is None


@pytest.mark.asyncio
async def test_breach_summary_falls_back_to_judgment_reasoning():
    breach = _breach_verdict()
    breach["subject"]["summary"] = ""
    breach["judgment"]["reasoning"] = "fallback reasoning text"
    client = _make_client(portfolio_rows=[], breach_rows=[breach], cases=[])

    view = await fetch_situation_board(client)

    assert view.recent_breaches[0].summary == "fallback reasoning text"


@pytest.mark.asyncio
async def test_no_breaches_yields_empty_list():
    client = _make_client(portfolio_rows=[], breach_rows=[], cases=[])
    view = await fetch_situation_board(client)
    assert view.recent_breaches == []


# ------------------------------------------------------------------ #
# Queue composition                                                    #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_queue_section_reuses_case_bench_fetch():
    """Active queue sub-view comes from the same fetch_case_bench helper
    the bench uses — single source of truth for case projection."""
    cases = [
        {"id": "c-1", "priority": "P1", "service": "fraud-detect", "state": "pending",
         "created_at": "2026-04-30T10:00:00Z", "underlying_verdict": "v"},
    ]
    client = _make_client(portfolio_rows=[], breach_rows=[], cases=cases)

    view = await fetch_situation_board(client)

    assert len(view.queue.flat) == 1
    assert view.queue.flat[0].case_id == "c-1"


# ------------------------------------------------------------------ #
# Error handling                                                       #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_portfolio_connection_failed_raises_core_unreachable():
    client = _make_client(portfolio_status=0, breach_rows=[], cases=[])
    with pytest.raises(CoreUnreachableError):
        await fetch_situation_board(client)


@pytest.mark.asyncio
async def test_breach_connection_failed_raises_core_unreachable():
    client = _make_client(portfolio_rows=[], breach_status=0, cases=[])
    with pytest.raises(CoreUnreachableError):
        await fetch_situation_board(client)


@pytest.mark.asyncio
async def test_cases_connection_failed_raises_core_unreachable():
    """Connection failure on the case-bench sub-fetch propagates as
    CoreUnreachableError (raised by fetch_case_bench, inherits BriefError
    so the situation-board widget's catch covers it)."""
    client = _make_client(portfolio_rows=[], breach_rows=[], cases_status=0)
    with pytest.raises(CoreUnreachableError):
        await fetch_situation_board(client)


@pytest.mark.asyncio
async def test_portfolio_other_error_raises_situation_board_error():
    client = _make_client(portfolio_status=500, breach_rows=[], cases=[])
    with pytest.raises(SituationBoardError):
        await fetch_situation_board(client)


@pytest.mark.asyncio
async def test_breach_other_error_raises_situation_board_error():
    client = _make_client(portfolio_rows=[], breach_status=500, cases=[])
    with pytest.raises(SituationBoardError):
        await fetch_situation_board(client)


# ------------------------------------------------------------------ #
# R5 Pass 3 defensive coverage                                         #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_non_dict_portfolio_row_returns_none_snapshot():
    """A malformed assessment row (string, list, None instead of dict)
    must not crash with AttributeError — it would escape the SRE error
    envelope and surface as an unhandled Textual exception. Fall through
    to the Waiting placeholder."""
    client = _make_client(
        portfolio_rows=["unexpected_string"],
        breach_rows=[],
        cases=[],
    )
    view = await fetch_situation_board(client)
    assert view.portfolio is None


@pytest.mark.asyncio
async def test_non_dict_portfolio_data_payload_returns_none_snapshot():
    """Same defence one level deeper — `data` must be a dict; a list or
    other non-dict survives upstream and would crash on `.get`."""
    bad = {
        "id": "asm-bad",
        "kind": "portfolio_status",
        "created_at": "2026-04-30T10:30:00Z",
        "data": ["not", "a", "dict"],
    }
    client = _make_client(portfolio_rows=[bad], breach_rows=[], cases=[])
    view = await fetch_situation_board(client)
    assert view.portfolio is None


@pytest.mark.asyncio
async def test_non_dict_breach_row_dropped_silently():
    """A malformed breach verdict (string/None) is filtered out before
    sort/projection so the panel doesn't crash on `.get`."""
    good = _breach_verdict(verdict_id="vrd-good")
    client = _make_client(
        portfolio_rows=[],
        breach_rows=[good, "not_a_dict", None],
        cases=[],
    )
    view = await fetch_situation_board(client)
    assert [b.verdict_id for b in view.recent_breaches] == ["vrd-good"]


@pytest.mark.asyncio
async def test_breach_with_non_dict_subject_judgment_metadata_does_not_crash():
    """Subject/judgment/metadata as non-dict (string, list, None) flow
    through `_safe_dict` to defaults — projection emits a BreachEvent
    with safe values rather than raising AttributeError."""
    weird = {
        "id": "vrd-weird",
        "service": "fraud-detect",
        "verdict_type": "quality_breach",
        "created_at": "2026-04-30T10:25:00Z",
        "subject": "not_a_dict",
        "judgment": ["also", "not"],
        "metadata": None,
    }
    client = _make_client(portfolio_rows=[], breach_rows=[weird], cases=[])
    view = await fetch_situation_board(client)
    event = view.recent_breaches[0]
    assert event.verdict_id == "vrd-weird"
    assert event.service == "fraud-detect"  # top-level service still surfaces
    assert event.summary == ""
    assert event.severity is None


@pytest.mark.asyncio
async def test_portfolio_counts_not_validated_against_total():
    """Schema drift: per-status counts that don't sum to total_services
    are surfaced verbatim. Pin the no-sanity-check contract so a future
    refactor can't silently drop or override the producer's data."""
    misaligned = _portfolio_assessment(
        total_services=5,
        healthy_count=10,  # > total
        warning_count=0,
        critical_count=0,
        exhausted_count=0,
    )
    client = _make_client(portfolio_rows=[misaligned], breach_rows=[], cases=[])
    view = await fetch_situation_board(client)
    assert view.portfolio is not None
    assert view.portfolio.total_services == 5
    assert view.portfolio.healthy == 10  # surfaced as-is, no clamping


# ------------------------------------------------------------------ #
# Renderer                                                             #
# ------------------------------------------------------------------ #

class TestRenderSituationBoard:
    def _view(self, **overrides) -> SituationBoardView:
        case = CaseSummary(
            case_id="c-1",
            priority="P1",
            service="fraud-detect",
            state="pending",
            created_at="2026-04-30T10:00:00Z",
            age_minutes=30,
            briefing="",
        )
        defaults = dict(
            portfolio=PortfolioSnapshot(
                total_services=5,
                healthy=3,
                warning=1,
                critical=1,
                exhausted=0,
                captured_at="2026-04-30T10:30:00Z",
            ),
            recent_breaches=[
                BreachEvent(
                    verdict_id="vrd-1",
                    service="fraud-detect",
                    summary="reversal rate breach",
                    created_at="2026-04-30T10:25:00Z",
                    severity="high",
                ),
            ],
            queue=CaseBenchView(
                ordered_priorities=["P1"],
                cases_by_priority={"P1": [case]},
                flat=[case],
            ),
        )
        defaults.update(overrides)
        return SituationBoardView(**defaults)

    def test_header_present(self):
        text = render_situation_board(self._view())
        assert "# Situation board" in text

    def test_portfolio_section_renders_counts(self):
        text = render_situation_board(self._view())
        assert "## Portfolio" in text
        assert "Services: 5" in text
        assert "Healthy:   3" in text
        assert "Warning:   1" in text
        assert "Critical:  1" in text

    def test_portfolio_placeholder_when_none(self):
        text = render_situation_board(self._view(portfolio=None))
        assert "Waiting for portfolio data." in text

    def test_breach_section_renders_each_event(self):
        text = render_situation_board(self._view())
        assert "## Recent quality breaches" in text
        assert "fraud-detect" in text
        assert "[high]" in text
        assert "reversal rate breach" in text

    def test_breach_placeholder_when_empty(self):
        text = render_situation_board(self._view(recent_breaches=[]))
        assert "No recent quality breaches." in text

    def test_queue_section_renders_priority_counts(self):
        text = render_situation_board(self._view())
        assert "## Active queue (1)" in text
        assert "P1: 1" in text

    def test_queue_placeholder_when_empty(self):
        text = render_situation_board(self._view(queue=CaseBenchView()))
        assert "No active cases." in text
