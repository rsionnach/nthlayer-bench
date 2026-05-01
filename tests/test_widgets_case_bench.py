"""Tests for ``nthlayer_bench.widgets.case_bench.CaseBenchPanel``."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from textual.app import App, ComposeResult
from textual.widgets import ListView, Static

from nthlayer_bench.sre.case_bench import (
    CaseBenchError,
    CaseBenchView,
    CaseSummary,
    CoreUnreachableError,
)
from nthlayer_bench.widgets.case_bench import CaseBenchPanel


class _Harness(App):
    def __init__(self, panel: CaseBenchPanel) -> None:
        super().__init__()
        self._panel = panel
        self.last_selection: CaseSummary | None = None

    def compose(self) -> ComposeResult:
        yield self._panel

    def on_case_bench_panel_case_selected(
        self, event: CaseBenchPanel.CaseSelected
    ) -> None:
        self.last_selection = event.summary


def _summary(case_id: str, *, priority: str = "P1", age: int = 5) -> CaseSummary:
    return CaseSummary(
        case_id=case_id,
        priority=priority,
        service="fraud-detect",
        state="pending",
        created_at="2026-04-30T10:00:00Z",
        age_minutes=age,
        briefing="",
    )


def _view(*summaries: CaseSummary) -> CaseBenchView:
    cases_by_priority: dict[str, list[CaseSummary]] = {}
    for s in summaries:
        cases_by_priority.setdefault(s.priority, []).append(s)
    ordered = [p for p in ("P0", "P1", "P2", "P3") if p in cases_by_priority]
    flat = [s for p in ordered for s in cases_by_priority[p]]
    return CaseBenchView(
        ordered_priorities=ordered,
        cases_by_priority=cases_by_priority,
        flat=flat,
    )


# ------------------------------------------------------------------ #
# Rendering                                                            #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_widget_renders_priority_counts_in_status_line():
    """Status line summarises each bucket's count so the operator sees
    the queue shape at a glance."""
    panel = CaseBenchPanel(AsyncMock())
    app = _Harness(panel)
    view = _view(_summary("c-p0", priority="P0"), _summary("c-p1", priority="P1"))
    with patch(
        "nthlayer_bench.widgets.case_bench.fetch_case_bench",
        new=AsyncMock(return_value=view),
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            status = str(panel.query_one("#status", Static).content)
            assert "P0: 1" in status
            assert "P1: 1" in status


@pytest.mark.asyncio
async def test_widget_renders_no_active_cases_for_empty_view():
    panel = CaseBenchPanel(AsyncMock())
    app = _Harness(panel)
    with patch(
        "nthlayer_bench.widgets.case_bench.fetch_case_bench",
        new=AsyncMock(return_value=CaseBenchView()),
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            status = str(panel.query_one("#status", Static).content)
            assert status == "No active cases."


@pytest.mark.asyncio
async def test_widget_populates_listview_with_headers_and_rows():
    """ListView gets one row per priority header + one row per case
    so cursor navigation flows naturally through the queue."""
    panel = CaseBenchPanel(AsyncMock())
    app = _Harness(panel)
    view = _view(
        _summary("c-p0-a", priority="P0"),
        _summary("c-p1-a", priority="P1"),
        _summary("c-p1-b", priority="P1"),
    )
    with patch(
        "nthlayer_bench.widgets.case_bench.fetch_case_bench",
        new=AsyncMock(return_value=view),
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            list_view = panel.query_one("#case-list", ListView)
            ids = [child.id for child in list_view.children]
            # Two priority headers + three case rows
            assert "hdr-P0" in ids
            assert "hdr-P1" in ids
            assert "case-c-p0-a" in ids
            assert "case-c-p1-a" in ids
            assert "case-c-p1-b" in ids


# ------------------------------------------------------------------ #
# Error states                                                         #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_widget_renders_inline_error_for_core_unreachable():
    panel = CaseBenchPanel(AsyncMock())
    app = _Harness(panel)
    with patch(
        "nthlayer_bench.widgets.case_bench.fetch_case_bench",
        new=AsyncMock(side_effect=CoreUnreachableError({"detail": "x"})),
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            err = str(panel.query_one("#error", Static).content)
            status = str(panel.query_one("#status", Static).content)
            assert "unreachable" in err.lower()
            assert status == ""  # cleared on error


@pytest.mark.asyncio
async def test_widget_renders_inline_error_for_case_bench_error():
    panel = CaseBenchPanel(AsyncMock())
    app = _Harness(panel)
    with patch(
        "nthlayer_bench.widgets.case_bench.fetch_case_bench",
        new=AsyncMock(side_effect=CaseBenchError("server explosion")),
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            err = str(panel.query_one("#error", Static).content)
            assert "Bench unavailable" in err
            assert "server explosion" in err


# ------------------------------------------------------------------ #
# Selection → CaseSelected message                                     #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_selection_posts_case_selected_message():
    """Operator picks a case row → CaseBenchPanel posts CaseSelected
    carrying the full summary so the host screen can push case-detail
    without re-fetching."""
    panel = CaseBenchPanel(AsyncMock())
    app = _Harness(panel)
    summary = _summary("c-target")
    view = _view(summary)
    with patch(
        "nthlayer_bench.widgets.case_bench.fetch_case_bench",
        new=AsyncMock(return_value=view),
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            list_view = panel.query_one("#case-list", ListView)

            # Find the case row's index (skip the priority header).
            case_index = next(
                i for i, child in enumerate(list_view.children)
                if child.id == "case-c-target"
            )
            list_view.index = case_index
            list_view.action_select_cursor()
            await pilot.pause()
            await pilot.pause()

            assert app.last_selection is not None
            assert app.last_selection.case_id == "c-target"


@pytest.mark.asyncio
async def test_selection_on_priority_header_does_not_post_message():
    """Priority headers are interleaved into the ListView for layout but
    are not selectable cases. Selecting one must not post a CaseSelected
    (which would later confuse the host screen's push logic)."""
    panel = CaseBenchPanel(AsyncMock())
    app = _Harness(panel)
    view = _view(_summary("c-1", priority="P1"))
    with patch(
        "nthlayer_bench.widgets.case_bench.fetch_case_bench",
        new=AsyncMock(return_value=view),
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            list_view = panel.query_one("#case-list", ListView)

            header_index = next(
                i for i, child in enumerate(list_view.children)
                if child.id == "hdr-P1"
            )
            list_view.index = header_index
            list_view.action_select_cursor()
            await pilot.pause()
            await pilot.pause()

            assert app.last_selection is None


# ------------------------------------------------------------------ #
# Lifecycle                                                            #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_selection_on_empty_listview_does_not_crash():
    """Pressing enter on an empty queue must not crash and must not post
    a CaseSelected. Textual's Selected event isn't fired for an empty
    list, but pin the contract so a future ListView change can't silently
    introduce a NoneType crash."""
    panel = CaseBenchPanel(AsyncMock())
    app = _Harness(panel)
    with patch(
        "nthlayer_bench.widgets.case_bench.fetch_case_bench",
        new=AsyncMock(return_value=CaseBenchView()),
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            list_view = panel.query_one("#case-list", ListView)
            list_view.action_select_cursor()
            await pilot.pause()

            assert app.last_selection is None


# ------------------------------------------------------------------ #
# Defensive projection paths                                           #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_widget_handles_blank_case_id_without_listitem_collision():
    """A case with a missing/blank id reaches the panel; both ListView
    and the items_by_id map must tolerate the blank without raising
    duplicate-id errors when only one such case is present."""
    panel = CaseBenchPanel(AsyncMock())
    app = _Harness(panel)
    blank = CaseSummary(
        case_id="",
        priority="P1",
        service="unknown",
        state="pending",
        created_at="",
        age_minutes=None,
        briefing="",
    )
    view = _view(blank)
    with patch(
        "nthlayer_bench.widgets.case_bench.fetch_case_bench",
        new=AsyncMock(return_value=view),
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            # Panel renders without raising; operator sees a placeholder row.
            assert "case-" in panel._items_by_id


@pytest.mark.asyncio
async def test_widget_skips_reentrant_refresh_when_previous_in_flight():
    panel = CaseBenchPanel(AsyncMock())
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
    panel = CaseBenchPanel(AsyncMock())
    app = _Harness(panel)
    with patch(
        "nthlayer_bench.widgets.case_bench.fetch_case_bench",
        new=AsyncMock(return_value=CaseBenchView()),
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            assert panel._timer is not None
            await panel.remove()
            await pilot.pause()
            assert panel._timer is None
