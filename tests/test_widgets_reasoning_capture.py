"""Tests for ``nthlayer_bench.widgets.reasoning_capture.ReasoningCapturePanel``."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input, Label, Static

from nthlayer_bench.sre.reasoning_capture import (
    CaseNotFoundError,
    CoreUnreachableError,
    OperatorNote,
    ReasoningCaptureError,
)
from nthlayer_bench.widgets.reasoning_capture import ReasoningCapturePanel


class _Harness(App):
    def __init__(self, panel: ReasoningCapturePanel) -> None:
        super().__init__()
        self._panel = panel

    def compose(self) -> ComposeResult:
        yield self._panel


def _note(verdict_id: str, text: str = "test", author: str = "alice") -> OperatorNote:
    return OperatorNote(
        verdict_id=verdict_id,
        case_id="case-123",
        author=author,
        text=text,
        created_at="2026-04-30T10:00:00Z",
    )


# ------------------------------------------------------------------ #
# Note feed rendering                                                  #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_widget_renders_existing_notes_on_mount():
    panel = ReasoningCapturePanel(AsyncMock(), "case-123")
    app = _Harness(panel)
    notes = [
        _note("vrd-1", "first observation", author="alice"),
        _note("vrd-2", "second observation", author="bob"),
    ]
    with patch(
        "nthlayer_bench.widgets.reasoning_capture.fetch_operator_notes",
        new=AsyncMock(return_value=notes),
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            labels = [
                str(label.content) for label in panel.query(".note-line")
            ]
            assert any("first observation" in line and "alice" in line for line in labels)
            assert any("second observation" in line and "bob" in line for line in labels)


@pytest.mark.asyncio
async def test_widget_renders_placeholder_when_no_notes():
    panel = ReasoningCapturePanel(AsyncMock(), "case-123")
    app = _Harness(panel)
    with patch(
        "nthlayer_bench.widgets.reasoning_capture.fetch_operator_notes",
        new=AsyncMock(return_value=[]),
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            labels = [
                str(label.content) for label in panel.query(".note-line")
            ]
            assert any("(no notes yet)" in line for line in labels)


# ------------------------------------------------------------------ #
# Submit flow                                                          #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_widget_submits_note_via_input_and_clears_on_success():
    """End-to-end happy path: operator types a note, presses enter,
    submit_operator_note is awaited, the input clears, the notes list
    refreshes."""
    panel = ReasoningCapturePanel(AsyncMock(), "case-123", author="alice@x")
    app = _Harness(panel)

    submit_mock = AsyncMock(return_value=_note("vrd-new", "investigated"))
    fetch_mock = AsyncMock(return_value=[])
    with patch(
        "nthlayer_bench.widgets.reasoning_capture.submit_operator_note",
        new=submit_mock,
    ), patch(
        "nthlayer_bench.widgets.reasoning_capture.fetch_operator_notes",
        new=fetch_mock,
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()

            input_widget = panel.query_one("#note-input", Input)
            input_widget.value = "investigated, looks like the deploy"
            await input_widget.action_submit()
            await pilot.pause()
            await pilot.pause()

            submit_mock.assert_awaited_once()
            args, kwargs = submit_mock.await_args
            # client, case_id, text positional; author kwarg
            assert args[1] == "case-123"
            assert args[2] == "investigated, looks like the deploy"
            assert kwargs["author"] == "alice@x"

            # Input cleared after success.
            assert input_widget.value == ""


@pytest.mark.asyncio
async def test_widget_drops_empty_input_silently():
    """Empty / whitespace-only submission is a no-op — never sent to
    core. Operator typing then accidentally hitting enter twice on a
    newline shouldn't post empty notes."""
    panel = ReasoningCapturePanel(AsyncMock(), "case-123")
    app = _Harness(panel)

    submit_mock = AsyncMock()
    with patch(
        "nthlayer_bench.widgets.reasoning_capture.submit_operator_note",
        new=submit_mock,
    ), patch(
        "nthlayer_bench.widgets.reasoning_capture.fetch_operator_notes",
        new=AsyncMock(return_value=[]),
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()

            input_widget = panel.query_one("#note-input", Input)
            input_widget.value = "   "  # whitespace only
            await input_widget.action_submit()
            await pilot.pause()

            submit_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_widget_keeps_input_and_shows_error_on_submit_failure():
    """Connection failure during submit must keep the operator's typed
    text intact and surface an inline error — never silently lose
    operator work."""
    panel = ReasoningCapturePanel(AsyncMock(), "case-123")
    app = _Harness(panel)

    submit_mock = AsyncMock(side_effect=CoreUnreachableError({"detail": "x"}))
    with patch(
        "nthlayer_bench.widgets.reasoning_capture.submit_operator_note",
        new=submit_mock,
    ), patch(
        "nthlayer_bench.widgets.reasoning_capture.fetch_operator_notes",
        new=AsyncMock(return_value=[]),
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()

            input_widget = panel.query_one("#note-input", Input)
            input_widget.value = "important observation"
            await input_widget.action_submit()
            await pilot.pause()
            await pilot.pause()

            # Input retained on error.
            assert input_widget.value == "important observation"
            # Inline error visible.
            err = str(panel.query_one("#error", Static).content)
            assert "core unreachable" in err.lower() or "unreachable" in err.lower()
            assert "try again" in err.lower()


@pytest.mark.asyncio
async def test_widget_keeps_input_on_reasoning_capture_error():
    panel = ReasoningCapturePanel(AsyncMock(), "case-123")
    app = _Harness(panel)
    submit_mock = AsyncMock(side_effect=ReasoningCaptureError("missing_fields"))
    with patch(
        "nthlayer_bench.widgets.reasoning_capture.submit_operator_note",
        new=submit_mock,
    ), patch(
        "nthlayer_bench.widgets.reasoning_capture.fetch_operator_notes",
        new=AsyncMock(return_value=[]),
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()

            input_widget = panel.query_one("#note-input", Input)
            input_widget.value = "draft"
            await input_widget.action_submit()
            await pilot.pause()
            await pilot.pause()

            assert input_widget.value == "draft"
            err = str(panel.query_one("#error", Static).content)
            assert "Submit failed" in err


@pytest.mark.asyncio
async def test_widget_double_submit_suppressed_while_in_flight():
    """Operator hammers enter — the second submission must not POST
    while the first is still in flight. Pin the in-flight guard."""
    panel = ReasoningCapturePanel(AsyncMock(), "case-123")
    app = _Harness(panel)

    gate = asyncio.Event()
    call_count = {"n": 0}

    async def slow_submit(client, case_id, text, *, author):
        call_count["n"] += 1
        await gate.wait()
        return _note("vrd-1", text)

    with patch(
        "nthlayer_bench.widgets.reasoning_capture.submit_operator_note",
        new=slow_submit,
    ), patch(
        "nthlayer_bench.widgets.reasoning_capture.fetch_operator_notes",
        new=AsyncMock(return_value=[]),
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            input_widget = panel.query_one("#note-input", Input)
            input_widget.value = "first"
            await input_widget.action_submit()
            await pilot.pause()
            # First submit blocked on gate. Try to submit again.
            input_widget.value = "second"
            await input_widget.action_submit()
            await pilot.pause()

            assert call_count["n"] == 1, "Second submit must not run while first in-flight"

            gate.set()
            await pilot.pause()
            await pilot.pause()


# ------------------------------------------------------------------ #
# Inline error states (read path)                                      #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_widget_renders_inline_error_for_case_not_found():
    panel = ReasoningCapturePanel(AsyncMock(), "case-missing")
    app = _Harness(panel)
    with patch(
        "nthlayer_bench.widgets.reasoning_capture.fetch_operator_notes",
        new=AsyncMock(side_effect=CaseNotFoundError("case-missing")),
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            err = str(panel.query_one("#error", Static).content)
            assert "not found" in err


@pytest.mark.asyncio
async def test_widget_renders_inline_error_for_core_unreachable():
    panel = ReasoningCapturePanel(AsyncMock(), "case-123")
    app = _Harness(panel)
    with patch(
        "nthlayer_bench.widgets.reasoning_capture.fetch_operator_notes",
        new=AsyncMock(side_effect=CoreUnreachableError({"detail": "x"})),
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            err = str(panel.query_one("#error", Static).content)
            assert "unreachable" in err.lower()


# ------------------------------------------------------------------ #
# Lifecycle                                                            #
# ------------------------------------------------------------------ #

# ------------------------------------------------------------------ #
# Bead 9b: write-queue integration                                     #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_widget_enqueues_note_when_core_unreachable_during_submit():
    """Bead 9b: instead of leaving the operator to retry by hand on
    core-unreachable, the panel builds a verdict from the cached case
    and enqueues it on the app's write queue."""
    from nthlayer_bench.sre.reasoning_capture import CoreUnreachableError
    from nthlayer_bench.sre.write_queue import WriteQueue

    queue = WriteQueue()
    panel = ReasoningCapturePanel(
        AsyncMock(), "case-123", author="alice", write_queue=queue,
    )
    app = _Harness(panel)

    case = {
        "id": "case-123",
        "service": "fraud-detect",
        "underlying_verdict": "vrd-anchor-001",
        "state": "pending",
        "priority": "P1",
    }

    with patch(
        "nthlayer_bench.widgets.reasoning_capture.fetch_case",
        new=AsyncMock(return_value=case),
    ), patch(
        "nthlayer_bench.widgets.reasoning_capture.fetch_operator_notes",
        new=AsyncMock(return_value=[]),
    ), patch(
        "nthlayer_bench.widgets.reasoning_capture.submit_operator_note",
        new=AsyncMock(side_effect=CoreUnreachableError({"x": "y"})),
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()  # first refresh populates _cached_case

            input_widget = panel.query_one("#note-input", Input)
            input_widget.value = "investigated, looks like the deploy"
            await input_widget.action_submit()
            await pilot.pause()
            await pilot.pause()

            # Note was queued, input cleared.
            assert len(queue) == 1
            assert input_widget.value == ""
            queued = queue.pending()[0]
            assert queued.case_id == "case-123"
            assert queued.verdict.judgment.reasoning == (
                "investigated, looks like the deploy"
            )

            # Status surfaces the queue depth — operator's at-a-glance
            # signal that they have outstanding writes.
            status = str(panel.query_one("#status", Static).content)
            assert "Pending submission" in status
            assert "1" in status


@pytest.mark.asyncio
async def test_widget_keeps_input_when_no_cached_case_yet():
    """Edge: operator submits before the first refresh has populated
    ``_cached_case``. Without a cached case the panel can't build a
    verdict offline — fall back to inline error + keep input (legacy
    Bead 7 behaviour)."""
    from nthlayer_bench.sre.reasoning_capture import CoreUnreachableError
    from nthlayer_bench.sre.write_queue import WriteQueue

    queue = WriteQueue()
    panel = ReasoningCapturePanel(AsyncMock(), "case-123", write_queue=queue)
    app = _Harness(panel)

    with patch(
        "nthlayer_bench.widgets.reasoning_capture.fetch_case",
        new=AsyncMock(side_effect=CoreUnreachableError({"x": "y"})),
    ), patch(
        "nthlayer_bench.widgets.reasoning_capture.fetch_operator_notes",
        new=AsyncMock(side_effect=CoreUnreachableError({"x": "y"})),
    ), patch(
        "nthlayer_bench.widgets.reasoning_capture.submit_operator_note",
        new=AsyncMock(side_effect=CoreUnreachableError({"x": "y"})),
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()
            assert panel._cached_case is None  # refresh failed; no cache

            input_widget = panel.query_one("#note-input", Input)
            input_widget.value = "first note before any refresh"
            await input_widget.action_submit()
            await pilot.pause()
            await pilot.pause()

            # No queue entry, input kept.
            assert len(queue) == 0
            assert input_widget.value == "first note before any refresh"
            err = str(panel.query_one("#error", Static).content)
            assert "unreachable" in err.lower()


@pytest.mark.asyncio
async def test_widget_renders_pending_count_in_status():
    """Operator-visible "Pending submission(s): N" — read on each
    refresh after an enqueue. Pin the rendering so the operator
    always knows whether they have outstanding writes."""
    from nthlayer_bench.sre.reasoning_capture import CoreUnreachableError
    from nthlayer_bench.sre.write_queue import WriteQueue

    queue = WriteQueue()
    panel = ReasoningCapturePanel(AsyncMock(), "case-123", write_queue=queue)
    app = _Harness(panel)

    case = {
        "id": "case-123",
        "service": "fraud-detect",
        "underlying_verdict": "vrd-anchor-001",
        "state": "pending",
        "priority": "P1",
    }

    with patch(
        "nthlayer_bench.widgets.reasoning_capture.fetch_case",
        new=AsyncMock(return_value=case),
    ), patch(
        "nthlayer_bench.widgets.reasoning_capture.fetch_operator_notes",
        new=AsyncMock(return_value=[]),
    ), patch(
        "nthlayer_bench.widgets.reasoning_capture.submit_operator_note",
        new=AsyncMock(side_effect=CoreUnreachableError({"x": "y"})),
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.pause()

            # Submit two notes — both enqueued (core unreachable).
            input_widget = panel.query_one("#note-input", Input)
            input_widget.value = "note 1"
            await input_widget.action_submit()
            await pilot.pause()
            input_widget.value = "note 2"
            await input_widget.action_submit()
            await pilot.pause()
            await pilot.pause()

            assert len(queue) == 2
            # Trigger a refresh so the pending-status renderer fires.
            await panel._refresh()
            await pilot.pause()
            status = str(panel.query_one("#status", Static).content)
            assert "Pending submissions: 2" in status


@pytest.mark.asyncio
async def test_widget_skips_reentrant_refresh_when_previous_in_flight():
    panel = ReasoningCapturePanel(AsyncMock(), "case-123")
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
    panel = ReasoningCapturePanel(AsyncMock(), "case-123")
    app = _Harness(panel)
    with patch(
        "nthlayer_bench.widgets.reasoning_capture.fetch_operator_notes",
        new=AsyncMock(return_value=[]),
    ):
        async with app.run_test() as pilot:
            await pilot.pause()
            assert panel._timer is not None
            await panel.remove()
            await pilot.pause()
            assert panel._timer is None
