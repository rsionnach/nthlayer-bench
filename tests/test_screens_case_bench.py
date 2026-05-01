"""Tests for ``CaseBenchScreen`` and the case-bench → case-detail flow."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from textual.app import App, ComposeResult
from textual.widgets import ListView

from nthlayer_bench.screens.case_bench import CaseBenchScreen
from nthlayer_bench.screens.case_detail import CaseDetailScreen
from nthlayer_bench.sre.brief import PagingBrief
from nthlayer_bench.sre.case_bench import CaseBenchView, CaseSummary
from nthlayer_bench.widgets.case_bench import CaseBenchPanel


class _Harness(App):
    def __init__(self, screen: CaseBenchScreen) -> None:
        super().__init__()
        self._screen = screen

    def compose(self) -> ComposeResult:
        return iter([])

    def on_mount(self) -> None:
        self.push_screen(self._screen)


def _summary(case_id: str = "case-target") -> CaseSummary:
    return CaseSummary(
        case_id=case_id,
        priority="P1",
        service="fraud-detect",
        state="pending",
        created_at="2026-04-30T10:00:00Z",
        age_minutes=10,
        briefing="reversal rate breach",
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


def _brief(case_id: str = "case-target") -> PagingBrief:
    return PagingBrief(
        case_id=case_id,
        service="fraud-detect",
        severity=2,
        summary="s",
        state="triage_complete",
        awaiting=["correlation", "remediation"],
    )


# ------------------------------------------------------------------ #
# Mount                                                                #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_case_bench_screen_mounts_panel():
    """CaseBenchScreen yields exactly one CaseBenchPanel."""
    client = AsyncMock()
    screen = CaseBenchScreen(client)
    app = _Harness(screen)
    with patch(
        "nthlayer_bench.widgets.case_bench.fetch_case_bench",
        new=AsyncMock(return_value=CaseBenchView()),
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            panels = list(screen.query(CaseBenchPanel))
            assert len(panels) == 1


# ------------------------------------------------------------------ #
# Selection → push CaseDetailScreen                                    #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_selecting_case_pushes_case_detail_screen():
    """End-to-end navigation: operator selects a row in the bench →
    CaseBenchScreen catches CaseSelected → pushes CaseDetailScreen."""
    client = AsyncMock()
    screen = CaseBenchScreen(client)
    app = _Harness(screen)
    summary = _summary("case-target")
    view = _view(summary)
    with patch(
        "nthlayer_bench.widgets.case_bench.fetch_case_bench",
        new=AsyncMock(return_value=view),
    ), patch(
        "nthlayer_bench.widgets.case_brief.build_paging_brief",
        new=AsyncMock(return_value=_brief("case-target")),
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            list_view = screen.query_one("#case-list", ListView)
            case_index = next(
                i for i, child in enumerate(list_view.children)
                if child.id == "case-case-target"
            )
            list_view.index = case_index
            list_view.action_select_cursor()
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, CaseDetailScreen)
            assert app.screen._case_id == "case-target"


@pytest.mark.asyncio
async def test_pop_then_reselect_pushes_again():
    """The double-push guard suppresses *consecutive* pushes onto an
    existing same-case detail. After a pop, selecting the same case
    again must push freshly — the guard's scope is "topmost is the
    same case", not "this case was ever shown"."""
    client = AsyncMock()
    screen = CaseBenchScreen(client)
    app = _Harness(screen)
    summary = _summary("case-target")
    with patch(
        "nthlayer_bench.widgets.case_bench.fetch_case_bench",
        new=AsyncMock(return_value=_view(summary)),
    ), patch(
        "nthlayer_bench.widgets.case_brief.build_paging_brief",
        new=AsyncMock(return_value=_brief("case-target")),
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()

            # First push: lands on detail.
            screen.post_message(CaseBenchPanel.CaseSelected(summary=summary))
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, CaseDetailScreen)

            # Pop back to the bench.
            await app.pop_screen()
            await pilot.pause()
            assert isinstance(app.screen, CaseBenchScreen)

            # Reselect the same case — must push freshly, not skip.
            screen.post_message(CaseBenchPanel.CaseSelected(summary=summary))
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, CaseDetailScreen)
            assert app.screen.case_id == "case-target"


@pytest.mark.asyncio
async def test_re_selecting_same_case_does_not_double_push():
    """Operator mashes enter on the same row — the screen stack must
    not accumulate duplicate CaseDetailScreens for the same case_id."""
    client = AsyncMock()
    screen = CaseBenchScreen(client)
    app = _Harness(screen)
    summary = _summary("case-target")
    with patch(
        "nthlayer_bench.widgets.case_bench.fetch_case_bench",
        new=AsyncMock(return_value=_view(summary)),
    ), patch(
        "nthlayer_bench.widgets.case_brief.build_paging_brief",
        new=AsyncMock(return_value=_brief("case-target")),
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()

            # First selection: pushes CaseDetailScreen.
            screen.post_message(CaseBenchPanel.CaseSelected(summary=summary))
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, CaseDetailScreen)
            stack_after_first = list(app._screen_stack)

            # Second selection arrives while the same case-detail is
            # still on top — no extra push.
            screen.post_message(CaseBenchPanel.CaseSelected(summary=summary))
            await pilot.pause()
            await pilot.pause()
            assert list(app._screen_stack) == stack_after_first
