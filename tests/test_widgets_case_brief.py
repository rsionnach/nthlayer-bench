"""Tests for ``nthlayer_bench.widgets.case_brief.CaseBriefPanel``.

Uses Textual's ``App.run_test()`` harness. The widget calls into
``build_paging_brief`` which is patched to return canned ``PagingBrief``
or raise ``BriefError`` subclasses. We assert on rendered widget content
per ``BriefState`` and on inline error rendering — not on visual layout.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from nthlayer_bench.sre.brief import (
    AnchorVerdictMissingError,
    CaseNotFoundError,
    CoreUnreachableError,
    PagingBrief,
)
from nthlayer_bench.widgets.case_brief import CaseBriefPanel


class _Harness(App):
    """Minimal Textual app that mounts a single CaseBriefPanel for tests."""

    def __init__(self, panel: CaseBriefPanel) -> None:
        super().__init__()
        self._panel = panel

    def compose(self) -> ComposeResult:
        yield self._panel


_WIDGET_IDS = ("header", "status", "summary", "cause", "blast", "recommended", "error")


async def _run_panel(
    *,
    return_value: PagingBrief | None = None,
    side_effect: Exception | None = None,
) -> dict[str, str]:
    """Mount the panel, patch ``build_paging_brief`` with the given mock
    behaviour, run until the first refresh completes, and return each
    widget's rendered text."""
    panel = CaseBriefPanel(AsyncMock(), "case-123")
    app = _Harness(panel)
    mock = AsyncMock(return_value=return_value, side_effect=side_effect)
    with patch("nthlayer_bench.widgets.case_brief.build_paging_brief", new=mock):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()  # let call_later fire and refresh complete
            return {
                wid: str(panel.query_one(f"#{wid}", Static).content)
                for wid in _WIDGET_IDS
            }


async def _run_with_brief(brief: PagingBrief) -> dict[str, str]:
    return await _run_panel(return_value=brief)


async def _run_with_exception(exc: Exception) -> dict[str, str]:
    return await _run_panel(side_effect=exc)


# ------------------------------------------------------------------ #
# State-aware rendering                                                #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_widget_renders_minimal_state_with_anchor_summary():
    brief = PagingBrief(
        case_id="case-123",
        service="fraud-detect",
        severity=None,
        summary="anchor reasoning",
        state="minimal",
        awaiting=["triage", "correlation", "remediation"],
    )
    text = await _run_with_brief(brief)
    assert "Severity: unknown" in text["header"]
    assert "minimal" in text["status"]
    assert "anchor reasoning" in text["summary"]
    # Cause placeholder shown for minimal state.
    assert "Awaiting triage" in text["cause"]


@pytest.mark.asyncio
async def test_widget_renders_triage_complete_state():
    brief = PagingBrief(
        case_id="case-123",
        service="fraud-detect",
        severity=2,
        summary="SEV-2 triage",
        blast_radius=["fraud-detect"],
        state="triage_complete",
        awaiting=["correlation", "remediation"],
    )
    text = await _run_with_brief(brief)
    assert "P2" in text["header"]
    assert "triage_complete" in text["status"]
    assert "Investigation in progress" in text["cause"]
    assert "fraud-detect" in text["blast"]


@pytest.mark.asyncio
async def test_widget_renders_investigation_complete_state():
    brief = PagingBrief(
        case_id="case-123",
        service="fraud-detect",
        severity=1,
        summary="SEV-1 triage",
        likely_cause="bad deploy",
        cause_confidence=0.74,
        state="investigation_complete",
        awaiting=["remediation"],
    )
    text = await _run_with_brief(brief)
    assert "investigation_complete" in text["status"]
    assert "bad deploy" in text["cause"]
    # Awaiting-remediation placeholder shown until remediation arrives.
    assert "awaiting remediation" in text["recommended"].lower()


@pytest.mark.asyncio
async def test_widget_renders_remediation_proposed_state_with_target():
    brief = PagingBrief(
        case_id="case-123",
        service="fraud-detect",
        severity=1,
        summary="SEV-1",
        likely_cause="bad deploy",
        cause_confidence=0.74,
        recommended_action="rollback",
        recommended_target="fraud-detect",
        state="remediation_proposed",
    )
    text = await _run_with_brief(brief)
    assert "P1" in text["header"]
    # remediation_proposed clears the status line.
    assert text["status"] == ""
    assert "rollback on fraud-detect" in text["recommended"]


@pytest.mark.asyncio
async def test_widget_renders_degraded_remediation_as_manual_intervention():
    brief = PagingBrief(
        case_id="case-123",
        service="fraud-detect",
        severity=2,
        summary="SEV-2",
        likely_cause="cause",
        cause_confidence=0.5,
        recommended_action=None,
        recommended_target=None,
        state="remediation_proposed",
    )
    text = await _run_with_brief(brief)
    assert "manual intervention required" in text["recommended"]


@pytest.mark.asyncio
async def test_widget_renders_recommended_action_without_target():
    brief = PagingBrief(
        case_id="case-123",
        service="fraud-detect",
        severity=2,
        summary="SEV-2",
        recommended_action="rollback",
        recommended_target=None,
        state="remediation_proposed",
    )
    text = await _run_with_brief(brief)
    assert text["recommended"] == "Recommended: rollback"


# ------------------------------------------------------------------ #
# Inline error states                                                  #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_widget_renders_inline_error_for_case_not_found():
    text = await _run_with_exception(CaseNotFoundError("case-missing"))
    assert "not found" in text["error"]
    # Positive-state fields cleared so stale data doesn't mislead.
    assert text["header"] == ""
    assert text["summary"] == ""


@pytest.mark.asyncio
async def test_widget_renders_inline_error_for_anchor_missing():
    text = await _run_with_exception(AnchorVerdictMissingError("vrd-missing"))
    assert "Data integrity" in text["error"] or "anchor" in text["error"].lower()


@pytest.mark.asyncio
async def test_widget_renders_inline_error_for_core_unreachable():
    text = await _run_with_exception(CoreUnreachableError({"detail": "x"}))
    assert "unreachable" in text["error"].lower()


# ------------------------------------------------------------------ #
# Refresh interval                                                     #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_widget_polls_at_configured_interval():
    """Acceptance criterion: panel refreshes every 5s. Verify the interval
    setting (we don't fast-forward 5s of wall-clock — the assertion is on
    the constant, not a timing race)."""
    from nthlayer_bench.widgets.case_brief import REFRESH_SECONDS

    assert REFRESH_SECONDS == 5.0


@pytest.mark.asyncio
async def test_widget_does_not_parse_rich_markup_in_verdict_strings():
    """Verdict text (judgment.reasoning, metadata.custom values) flows
    from LLM agents. A summary containing `[bold]` or unbalanced brackets
    must NOT be parsed as Rich markup — it should render literally.
    Pins the ``markup=False`` flag on the data-bearing Static widgets."""
    brief = PagingBrief(
        case_id="case-123",
        service="fraud-detect",
        severity=2,
        summary="error: [unexpected] bracket and [bold red]markup[/]",
        likely_cause="cause with [italic]formatting[/italic]",
        cause_confidence=0.5,
        recommended_action="rollback",
        recommended_target="[malicious] target",
        state="remediation_proposed",
    )
    text = await _run_with_brief(brief)

    # Text renders verbatim, no markup expansion or parse failure.
    assert "[unexpected]" in text["summary"]
    assert "[bold red]markup[/]" in text["summary"]
    assert "[italic]" in text["cause"]
    assert "[malicious] target" in text["recommended"]


# ------------------------------------------------------------------ #
# Reentrant refresh + unmount lifecycle                                #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_widget_skips_reentrant_refresh_when_previous_in_flight():
    """If a previous _refresh is still in flight (slow core, large
    descendant chain), a second invocation must not race the first — the
    lock skips the tick. Tested at the lock-semantics level directly,
    without mounting in a Textual app, since the invariant being asserted
    is about ``self._refresh_lock``, not rendering."""
    import asyncio

    panel = CaseBriefPanel(AsyncMock(), "case-123")
    gate = asyncio.Event()
    call_count = {"n": 0}

    async def slow_do_refresh():
        call_count["n"] += 1
        await gate.wait()

    panel._do_refresh = slow_do_refresh  # type: ignore[method-assign]

    first = asyncio.create_task(panel._refresh())
    # Yield once so first acquires the lock and parks on gate.
    await asyncio.sleep(0)

    # Second invocation must see the lock held and bail without calling _do_refresh.
    await panel._refresh()
    assert call_count["n"] == 1, "Second refresh must not invoke _do_refresh"

    gate.set()
    await first
    assert call_count["n"] == 1


@pytest.mark.asyncio
async def test_widget_unmount_stops_refresh_timer():
    """Spec line 201: when the user navigates away from the case, the
    widget's on_unmount must clear the interval. Capture the timer so the
    test can observe its stopped state."""
    panel = CaseBriefPanel(AsyncMock(), "case-123")
    app = _Harness(panel)
    brief = PagingBrief(
        case_id="case-123",
        service="fraud-detect",
        severity=2,
        summary="s",
        state="triage_complete",
        awaiting=["correlation", "remediation"],
    )
    with patch(
        "nthlayer_bench.widgets.case_brief.build_paging_brief",
        new=AsyncMock(return_value=brief),
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            assert panel._timer is not None
            timer = panel._timer
            await panel.remove()
            await pilot.pause()
            # Public-API assertion: on_unmount cleared the panel's timer
            # reference. The Textual-private attributes (timer._active,
            # timer._task) intentionally aren't asserted on; they would
            # break on Textual minor bumps without telling us anything
            # the public reference doesn't already cover.
            assert panel._timer is None
            del timer  # silence unused-variable hints; captured for future debugging only
