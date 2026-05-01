"""Tests for ``nthlayer_bench.widgets.situation_board.SituationBoardPanel``."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from nthlayer_bench.sre.case_bench import CaseBenchView
from nthlayer_bench.sre.situation_board import (
    BreachEvent,
    CoreUnreachableError,
    PortfolioSnapshot,
    SituationBoardError,
    SituationBoardView,
)
from nthlayer_bench.widgets.situation_board import SituationBoardPanel


class _Harness(App):
    def __init__(self, panel: SituationBoardPanel) -> None:
        super().__init__()
        self._panel = panel

    def compose(self) -> ComposeResult:
        yield self._panel


_WIDGET_IDS = ("body", "error")


async def _run_panel(
    *,
    return_value: SituationBoardView | None = None,
    side_effect: Exception | None = None,
) -> dict[str, str]:
    panel = SituationBoardPanel(AsyncMock())
    app = _Harness(panel)
    mock = AsyncMock(return_value=return_value, side_effect=side_effect)
    with patch("nthlayer_bench.widgets.situation_board.fetch_situation_board", new=mock):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            return {wid: str(panel.query_one(f"#{wid}", Static).content) for wid in _WIDGET_IDS}


def _populated_view() -> SituationBoardView:
    return SituationBoardView(
        portfolio=PortfolioSnapshot(
            total_services=4,
            healthy=2,
            warning=1,
            critical=1,
            exhausted=0,
            captured_at="2026-04-30T10:30:00Z",
        ),
        recent_breaches=[
            BreachEvent(
                verdict_id="vrd-1",
                service="fraud-detect",
                summary="reversal rate at 8%",
                created_at="2026-04-30T10:25:00Z",
                severity="high",
            )
        ],
        queue=CaseBenchView(),
    )


# ------------------------------------------------------------------ #
# Rendering                                                            #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_widget_renders_populated_dashboard():
    text = await _run_panel(return_value=_populated_view())
    assert "Situation board" in text["body"]
    assert "Services: 4" in text["body"]
    assert "fraud-detect" in text["body"]
    assert text["error"] == ""


@pytest.mark.asyncio
async def test_widget_renders_cold_start_placeholders():
    """Operator opens bench before workers have produced anything —
    portfolio, breaches, and queue are all empty. Renders placeholders
    rather than a half-empty layout."""
    empty_view = SituationBoardView()
    text = await _run_panel(return_value=empty_view)
    assert "Waiting for portfolio data." in text["body"]
    assert "No recent quality breaches." in text["body"]
    assert "No active cases." in text["body"]


# ------------------------------------------------------------------ #
# Inline error states                                                  #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_widget_renders_inline_error_for_core_unreachable():
    text = await _run_panel(side_effect=CoreUnreachableError({"detail": "x"}))
    assert "unreachable" in text["error"].lower()
    assert text["body"] == ""


@pytest.mark.asyncio
async def test_widget_renders_inline_error_for_situation_board_error():
    text = await _run_panel(side_effect=SituationBoardError("server down"))
    assert "Situation board unavailable" in text["error"]
    assert "server down" in text["error"]


# ------------------------------------------------------------------ #
# Markup escaping                                                      #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_widget_does_not_parse_rich_markup_in_breach_summaries():
    """Breach summaries embed verdict text from LLM agents — pin
    markup=False so stray `[bold]` doesn't reformat the dashboard."""
    view = _populated_view()
    view.recent_breaches = [
        BreachEvent(
            verdict_id="vrd-1",
            service="fraud-detect",
            summary="error: [unexpected] [bold red]markup[/]",
            created_at="2026-04-30T10:25:00Z",
            severity="high",
        )
    ]
    text = await _run_panel(return_value=view)
    assert "[unexpected]" in text["body"]
    assert "[bold red]markup[/]" in text["body"]


# ------------------------------------------------------------------ #
# Lifecycle                                                            #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_widget_skips_reentrant_refresh_when_previous_in_flight():
    panel = SituationBoardPanel(AsyncMock())
    gate = asyncio.Event()
    call_count = {"n": 0}

    async def slow_do_refresh():
        call_count["n"] += 1
        await gate.wait()

    panel._do_refresh = slow_do_refresh  # type: ignore[method-assign]

    first = asyncio.create_task(panel._refresh())
    await asyncio.sleep(0)
    await panel._refresh()
    assert call_count["n"] == 1
    gate.set()
    await first
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_widget_unmount_stops_refresh_timer():
    panel = SituationBoardPanel(AsyncMock())
    app = _Harness(panel)
    with patch(
        "nthlayer_bench.widgets.situation_board.fetch_situation_board",
        new=AsyncMock(return_value=SituationBoardView()),
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            assert panel._timer is not None
            await panel.remove()
            await pilot.pause()
            assert panel._timer is None
