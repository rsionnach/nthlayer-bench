"""Tests for ``CaseReviewScreen`` and the ``r``-key flow from
``CaseDetailScreen`` to ``CaseReviewScreen``."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from textual.app import App, ComposeResult

from nthlayer_bench.screens.case_detail import CaseDetailScreen
from nthlayer_bench.screens.case_review import CaseReviewScreen
from nthlayer_bench.sre.brief import PagingBrief
from nthlayer_bench.sre.post_incident import PostIncidentReview
from nthlayer_bench.widgets.case_review import CaseReviewPanel


class _Harness(App):
    def __init__(self, screen) -> None:
        super().__init__()
        self._screen = screen

    def compose(self) -> ComposeResult:
        return iter([])

    def on_mount(self) -> None:
        self.push_screen(self._screen)


def _brief() -> PagingBrief:
    return PagingBrief(
        case_id="case-123",
        service="fraud-detect",
        severity=2,
        summary="s",
        state="triage_complete",
        awaiting=["correlation", "remediation"],
    )


def _review() -> PostIncidentReview:
    return PostIncidentReview(
        case_id="case-123",
        service="fraud-detect",
        severity=2,
        state="resolved",
        duration_minutes=42,
    )


@pytest.mark.asyncio
async def test_review_screen_mounts_review_panel():
    """CaseReviewScreen yields exactly one CaseReviewPanel for the
    case_id passed to the constructor."""
    client = AsyncMock()
    screen = CaseReviewScreen(client, "case-123")
    app = _Harness(screen)
    with patch(
        "nthlayer_bench.widgets.case_review.build_post_incident_review",
        new=AsyncMock(return_value=_review()),
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            panels = list(screen.query(CaseReviewPanel))
            assert len(panels) == 1
            assert panels[0]._case_id == "case-123"


@pytest.mark.asyncio
async def test_r_key_on_case_detail_pushes_review_screen():
    """End-to-end navigation: CaseDetailScreen + 'r' → CaseReviewScreen
    is on the screen stack and shows the same case_id."""
    client = AsyncMock()
    detail = CaseDetailScreen(client, "case-123")
    app = _Harness(detail)
    with patch(
        "nthlayer_bench.widgets.case_brief.build_paging_brief",
        new=AsyncMock(return_value=_brief()),
    ), patch(
        "nthlayer_bench.widgets.case_review.build_post_incident_review",
        new=AsyncMock(return_value=_review()),
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            assert app.screen is detail
            await pilot.press("r")
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, CaseReviewScreen)
            assert app.screen._case_id == "case-123"


@pytest.mark.asyncio
async def test_escape_on_review_screen_returns_to_case_detail():
    """Symmetric navigation: pressing escape on the review pops back to
    the case-detail screen the operator came from."""
    client = AsyncMock()
    detail = CaseDetailScreen(client, "case-123")
    app = _Harness(detail)
    with patch(
        "nthlayer_bench.widgets.case_brief.build_paging_brief",
        new=AsyncMock(return_value=_brief()),
    ), patch(
        "nthlayer_bench.widgets.case_review.build_post_incident_review",
        new=AsyncMock(return_value=_review()),
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            await pilot.press("r")
            await pilot.pause()
            await pilot.pause()
            assert isinstance(app.screen, CaseReviewScreen)
            await pilot.press("escape")
            await pilot.pause()
            assert app.screen is detail
