"""Tests for ``SituationBoardScreen`` and the case-bench → situation
board navigation flow (``s`` key)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from textual.app import App, ComposeResult

from nthlayer_bench.screens.case_bench import CaseBenchScreen
from nthlayer_bench.screens.situation_board import SituationBoardScreen
from nthlayer_bench.sre.case_bench import CaseBenchView
from nthlayer_bench.sre.situation_board import SituationBoardView
from nthlayer_bench.widgets.situation_board import SituationBoardPanel


class _Harness(App):
    def __init__(self, screen) -> None:
        super().__init__()
        self._screen = screen

    def compose(self) -> ComposeResult:
        return iter([])

    def on_mount(self) -> None:
        self.push_screen(self._screen)


@pytest.mark.asyncio
async def test_situation_board_screen_mounts_panel():
    """SituationBoardScreen yields exactly one SituationBoardPanel."""
    screen = SituationBoardScreen(AsyncMock())
    app = _Harness(screen)
    with patch(
        "nthlayer_bench.widgets.situation_board.fetch_situation_board",
        new=AsyncMock(return_value=SituationBoardView()),
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            panels = list(screen.query(SituationBoardPanel))
            assert len(panels) == 1


@pytest.mark.asyncio
async def test_s_key_on_case_bench_pushes_situation_board_screen():
    """End-to-end navigation: CaseBenchScreen + 's' → SituationBoardScreen
    on the screen stack."""
    screen = CaseBenchScreen(AsyncMock())
    app = _Harness(screen)
    with patch(
        "nthlayer_bench.widgets.case_bench.fetch_case_bench",
        new=AsyncMock(return_value=CaseBenchView()),
    ), patch(
        "nthlayer_bench.widgets.situation_board.fetch_situation_board",
        new=AsyncMock(return_value=SituationBoardView()),
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            assert app.screen is screen
            await pilot.press("s")
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, SituationBoardScreen)


@pytest.mark.asyncio
async def test_escape_on_situation_board_returns_to_case_bench():
    """Symmetric navigation: pressing escape on the dashboard pops
    back to the case bench (the screen the operator came from)."""
    bench = CaseBenchScreen(AsyncMock())
    app = _Harness(bench)
    with patch(
        "nthlayer_bench.widgets.case_bench.fetch_case_bench",
        new=AsyncMock(return_value=CaseBenchView()),
    ), patch(
        "nthlayer_bench.widgets.situation_board.fetch_situation_board",
        new=AsyncMock(return_value=SituationBoardView()),
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            await pilot.press("s")
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, SituationBoardScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert app.screen is bench
