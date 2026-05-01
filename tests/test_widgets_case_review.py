"""Tests for ``nthlayer_bench.widgets.case_review.CaseReviewPanel``.

Mirrors the test approach from ``test_widgets_case_brief.py``: patch
``build_post_incident_review`` to canned ``PostIncidentReview`` (or
raise an error subclass) and assert on Static widget content.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from nthlayer_bench.sre.post_incident import (
    AnchorVerdictMissingError,
    CaseNotFoundError,
    CoreUnreachableError,
    PostIncidentReview,
    TimelineEntry,
)
from nthlayer_bench.widgets.case_review import CaseReviewPanel


class _Harness(App):
    def __init__(self, panel: CaseReviewPanel) -> None:
        super().__init__()
        self._panel = panel

    def compose(self) -> ComposeResult:
        yield self._panel


_WIDGET_IDS = ("body", "error")


async def _run_panel(
    *,
    return_value: PostIncidentReview | None = None,
    side_effect: Exception | None = None,
) -> dict[str, str]:
    panel = CaseReviewPanel(AsyncMock(), "case-123")
    app = _Harness(panel)
    mock = AsyncMock(return_value=return_value, side_effect=side_effect)
    with patch("nthlayer_bench.widgets.case_review.build_post_incident_review", new=mock):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            return {wid: str(panel.query_one(f"#{wid}", Static).content) for wid in _WIDGET_IDS}


def _review() -> PostIncidentReview:
    return PostIncidentReview(
        case_id="case-123",
        service="fraud-detect",
        severity=2,
        state="resolved",
        duration_minutes=42,
        timeline=[
            TimelineEntry(
                verdict_id="vrd-triage-001",
                timestamp="2026-04-28T10:00:00Z",
                actor="nthlayer-respond",
                role="triage",
                summary="SEV-2 triage",
                confidence=0.85,
            )
        ],
    )


# ------------------------------------------------------------------ #
# Rendering                                                            #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_widget_renders_review_body():
    text = await _run_panel(return_value=_review())
    assert "Post-incident review: case-123" in text["body"]
    assert "Service: fraud-detect" in text["body"]
    assert "Severity: P2" in text["body"]
    assert "Duration: 42 minutes" in text["body"]
    assert text["error"] == ""


@pytest.mark.asyncio
async def test_widget_renders_in_progress_state_with_draft_banner():
    review = _review()
    review.state = "in_progress"
    text = await _run_panel(return_value=review)
    assert "DRAFT" in text["body"]


# ------------------------------------------------------------------ #
# Inline error states                                                  #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_widget_renders_inline_error_for_case_not_found():
    text = await _run_panel(side_effect=CaseNotFoundError("case-missing"))
    assert "not found" in text["error"]
    assert text["body"] == ""


@pytest.mark.asyncio
async def test_widget_renders_inline_error_for_anchor_missing():
    text = await _run_panel(side_effect=AnchorVerdictMissingError("vrd-missing"))
    assert "Data integrity" in text["error"]


@pytest.mark.asyncio
async def test_widget_renders_inline_error_for_core_unreachable():
    text = await _run_panel(side_effect=CoreUnreachableError({"detail": "x"}))
    assert "unreachable" in text["error"].lower()


# ------------------------------------------------------------------ #
# Markup escaping                                                      #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_widget_does_not_parse_rich_markup_in_review_body():
    """Verdict summaries embed in the timeline; LLM-produced text could
    contain `[bold]` etc. Pin the markup=False rule."""
    review = _review()
    review.timeline = [
        TimelineEntry(
            verdict_id="vrd-triage-001",
            timestamp="2026-04-28T10:00:00Z",
            actor="nthlayer-respond",
            role="triage",
            summary="error: [unexpected] [bold red]markup[/]",
            confidence=0.85,
        )
    ]
    text = await _run_panel(return_value=review)
    assert "[unexpected]" in text["body"]
    assert "[bold red]markup[/]" in text["body"]


# ------------------------------------------------------------------ #
# Lifecycle                                                            #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_widget_skips_reentrant_refresh_when_previous_in_flight():
    panel = CaseReviewPanel(AsyncMock(), "case-123")
    gate = asyncio.Event()
    call_count = {"n": 0}

    async def slow_do_refresh():
        call_count["n"] += 1
        await gate.wait()

    panel._do_refresh = slow_do_refresh  # type: ignore[method-assign]

    first = asyncio.create_task(panel._refresh())
    await asyncio.sleep(0)
    await panel._refresh()
    assert call_count["n"] == 1, "Second refresh must not invoke _do_refresh"
    gate.set()
    await first
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_widget_unmount_stops_refresh_timer():
    panel = CaseReviewPanel(AsyncMock(), "case-123")
    app = _Harness(panel)
    with patch(
        "nthlayer_bench.widgets.case_review.build_post_incident_review",
        new=AsyncMock(return_value=_review()),
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            assert panel._timer is not None
            await panel.remove()
            await pilot.pause()
            assert panel._timer is None
