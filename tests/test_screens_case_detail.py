"""Tests for ``nthlayer_bench.screens.case_detail.CaseDetailScreen``.

The screen is a thin shell around ``CaseBriefPanel``: it composes the
left-pane context placeholder and the right-pane brief, binds escape to
``app.pop_screen``, and renders a header/footer chrome. We assert on
mount, on the bindings, on the panel being present, and on the navigation
contract (escape pops back to the prior screen).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from nthlayer_bench.screens.case_detail import CaseDetailScreen
from nthlayer_bench.sre.brief import PagingBrief
from nthlayer_bench.widgets.case_brief import CaseBriefPanel


class _Harness(App):
    """Minimal app that pushes a CaseDetailScreen for the given case_id."""

    def __init__(self, screen: CaseDetailScreen) -> None:
        super().__init__()
        self._screen = screen

    def compose(self) -> ComposeResult:
        # Yield nothing — the screen pushed in on_mount is the only
        # content. Textual's auto-default screen sits underneath the
        # pushed screen so escape pops back to a valid screen.
        return iter([])

    def on_mount(self) -> None:
        self.push_screen(self._screen)


def _make_brief(case_id: str = "case-123") -> PagingBrief:
    return PagingBrief(
        case_id=case_id,
        service="fraud-detect",
        severity=2,
        summary="reversal-rate breach",
        state="triage_complete",
        awaiting=["correlation", "remediation"],
    )


@pytest.mark.asyncio
async def test_case_detail_screen_mounts_brief_panel():
    """Composing the screen yields exactly one CaseBriefPanel for the
    case_id passed to the constructor."""
    client = AsyncMock()
    screen = CaseDetailScreen(client, "case-123")
    app = _Harness(screen)

    with patch(
        "nthlayer_bench.widgets.case_brief.build_paging_brief",
        new=AsyncMock(return_value=_make_brief()),
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            panels = list(screen.query(CaseBriefPanel))
            assert len(panels) == 1
            assert panels[0]._case_id == "case-123"


@pytest.mark.asyncio
async def test_case_detail_screen_renders_context_pane_with_case_id():
    """Left context pane must include the case_id so the operator can
    confirm orientation after navigation."""
    client = AsyncMock()
    screen = CaseDetailScreen(client, "case-XYZ-9")
    app = _Harness(screen)

    with patch(
        "nthlayer_bench.widgets.case_brief.build_paging_brief",
        new=AsyncMock(return_value=_make_brief("case-XYZ-9")),
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            id_widget = screen.query_one("#context-id", Static)
            assert "case-XYZ-9" in str(id_widget.content)


@pytest.mark.asyncio
async def test_case_detail_screen_escape_binding_pops_screen():
    """Escape must return to the prior screen — the bench's situation
    board / case bench / placeholder. Pin the binding so a future change
    doesn't silently strand operators in case detail."""
    client = AsyncMock()
    screen = CaseDetailScreen(client, "case-123")
    app = _Harness(screen)

    with patch(
        "nthlayer_bench.widgets.case_brief.build_paging_brief",
        new=AsyncMock(return_value=_make_brief()),
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            assert app.screen is screen
            await pilot.press("escape")
            await pilot.pause()
            assert app.screen is not screen


@pytest.mark.asyncio
async def test_case_detail_screen_push_pop_push_reuses_app_client():
    """Operator pages between cases: push CaseDetailScreen, pop, push
    another. The app's shared client must survive both screen lifecycles
    (no screen-level cleanup of the app client). Prevents a regression
    where a screen `on_unmount` closes the shared client by mistake."""
    from nthlayer_common.api_client import CoreAPIClient

    from nthlayer_bench.app import BenchApp
    from nthlayer_bench.sre.case_bench import CaseBenchView

    app = BenchApp(core_url="http://test:8000")

    with patch(
        "nthlayer_bench.widgets.case_brief.build_paging_brief",
        new=AsyncMock(return_value=_make_brief("case-A")),
    ), patch(
        # BenchApp pushes CaseBenchScreen on mount when no --case-id; the
        # case-bench panel polls fetch_case_bench. Empty view here so the
        # screen-stack assertions below aren't fighting the auto-poll.
        "nthlayer_bench.widgets.case_bench.fetch_case_bench",
        new=AsyncMock(return_value=CaseBenchView()),
    ):
        async with app.run_test() as pilot:
            client_first = app.client
            assert isinstance(client_first, CoreAPIClient)

            screen_a = CaseDetailScreen(client_first, "case-A")
            await app.push_screen(screen_a)
            await pilot.pause()
            await pilot.pause()
            assert app.screen is screen_a

            await app.pop_screen()
            await pilot.pause()
            assert app.screen is not screen_a

            # Push a second case-detail screen — app's client must still
            # be the same instance (not closed by the first pop).
            screen_b = CaseDetailScreen(app.client, "case-B")
            assert app.client is client_first  # idempotent property
            await app.push_screen(screen_b)
            await pilot.pause()
            await pilot.pause()
            assert app.screen is screen_b


@pytest.mark.asyncio
async def test_case_detail_screen_mounts_reasoning_capture_panel():
    """Bead 7: the reasoning-capture placeholder Static was replaced by
    a live ReasoningCapturePanel. Pin that the panel mounts in the left
    context pane and is wired to the same case_id as the brief panel."""
    from nthlayer_bench.widgets.reasoning_capture import ReasoningCapturePanel

    client = AsyncMock()
    screen = CaseDetailScreen(client, "case-123")
    app = _Harness(screen)

    with patch(
        "nthlayer_bench.widgets.case_brief.build_paging_brief",
        new=AsyncMock(return_value=_make_brief()),
    ), patch(
        # ReasoningCapturePanel polls fetch_operator_notes on mount;
        # patch with an empty list so the test doesn't depend on a
        # live core.
        "nthlayer_bench.widgets.reasoning_capture.fetch_operator_notes",
        new=AsyncMock(return_value=[]),
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            panels = list(screen.query(ReasoningCapturePanel))
            assert len(panels) == 1
            assert panels[0]._case_id == "case-123"
