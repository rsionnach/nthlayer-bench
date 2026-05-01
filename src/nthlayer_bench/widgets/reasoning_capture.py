"""Reasoning capture panel — operator notes for a case.

Mounts in the case-detail screen's left context pane. Renders the
chronological note feed and an input field for new notes; submitting
posts an ``operator_note`` verdict to core via ``submit_operator_note``.

Bench's first write path. On submit failure the typed text stays in the
input so the operator can retry — never silently lose operator work.
"""
from __future__ import annotations

import asyncio

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.timer import Timer
from textual.widgets import Input, Label, Static

from nthlayer_common.api_client import CoreAPIClient

from nthlayer_bench.sre.reasoning_capture import (
    AnchorVerdictMissingError,
    CaseNotFoundError,
    CoreUnreachableError,
    DEFAULT_AUTHOR,
    OperatorNote,
    ReasoningCaptureError,
    fetch_operator_notes,
    submit_operator_note,
)

REFRESH_SECONDS = 5.0


class ReasoningCapturePanel(Vertical):
    """Compact reasoning-capture panel for the case-detail left pane."""

    DEFAULT_CSS = """
    ReasoningCapturePanel {
        height: 1fr;
    }
    ReasoningCapturePanel #notes-title {
        text-style: bold;
        padding-bottom: 1;
    }
    ReasoningCapturePanel #notes-list {
        height: 1fr;
    }
    ReasoningCapturePanel #notes-list .note-line {
        padding-bottom: 1;
    }
    ReasoningCapturePanel #status {
        color: $text-muted;
    }
    ReasoningCapturePanel #error {
        color: $error;
        text-style: bold;
    }
    """

    def __init__(
        self,
        client: CoreAPIClient,
        case_id: str,
        *,
        author: str = DEFAULT_AUTHOR,
        refresh_seconds: float = REFRESH_SECONDS,
    ) -> None:
        super().__init__()
        self._client = client
        self._case_id = case_id
        self._author = author
        self._refresh_seconds = refresh_seconds
        self._timer: Timer | None = None
        self._refresh_lock = asyncio.Lock()
        # In-flight write guard: operator double-clicking the submit
        # binding must not trigger two POSTs for the same text.
        self._submit_in_flight = False

    def compose(self) -> ComposeResult:
        yield Static("Reasoning", id="notes-title", markup=False)
        yield VerticalScroll(id="notes-list")
        yield Input(placeholder="Add a note (Enter to submit)…", id="note-input")
        yield Static("", id="status", markup=False)
        yield Static("", id="error", markup=False)

    def on_mount(self) -> None:
        self._timer = self.set_interval(self._refresh_seconds, self._refresh)
        # Immediate first refresh so the operator sees existing notes
        # without a 5s delay on screen entry.
        self.call_later(self._refresh)

    def on_unmount(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    async def _refresh(self) -> None:
        if self._refresh_lock.locked():
            return
        async with self._refresh_lock:
            await self._do_refresh()

    async def _do_refresh(self) -> None:
        try:
            notes = await fetch_operator_notes(self._client, self._case_id)
        except CaseNotFoundError:
            self._render_error(f"Case {self._case_id!r} not found.")
            return
        except AnchorVerdictMissingError as exc:
            self._render_error(
                f"Data integrity issue: anchor verdict {exc} not found."
            )
            return
        except CoreUnreachableError:
            self._render_error("Core unreachable — retrying.")
            return
        except ReasoningCaptureError as exc:
            self._render_error(f"Notes unavailable: {exc}")
            return
        await self._render_notes(notes)

    async def _render_notes(self, notes: list[OperatorNote]) -> None:
        self.query_one("#error", Static).update("")
        notes_list = self.query_one("#notes-list", VerticalScroll)
        await notes_list.remove_children()
        if not notes:
            await notes_list.mount(
                Label("(no notes yet)", classes="note-line", markup=False)
            )
            return
        for note in notes:
            await notes_list.mount(
                Label(_format_note_line(note), classes="note-line", markup=False)
            )

    def _render_error(self, message: str) -> None:
        # Don't clear the input — the operator may have just typed
        # something they want to keep. Don't clear the notes list either:
        # stale notes are more useful than a blank pane during a glitch.
        self.query_one("#error", Static).update(message)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Operator pressed enter in the note input — submit the note.

        Guards: empty/whitespace-only input is dropped silently (no
        accidental empty-note POSTs); double-submit (operator hammers
        enter) is gated by ``_submit_in_flight`` so the same text only
        crosses the network once.
        """
        text = event.value.strip()
        if not text:
            return
        if self._submit_in_flight:
            return
        # Set the flag synchronously here, BEFORE scheduling the task,
        # to close the check-and-set window. Textual delivers messages
        # serially via the message pump but a future change to async
        # delivery would otherwise allow two enter-presses on the same
        # tick to both observe False. _do_submit's try/finally clears it.
        self._submit_in_flight = True
        # Async submission off the event handler — Textual handlers
        # complete synchronously, so spawn a task for the network round
        # trip. Errors are caught inside _do_submit; nothing escapes.
        asyncio.create_task(self._do_submit(event.input, text))

    async def _do_submit(self, input_widget: Input, text: str) -> None:
        status = self.query_one("#status", Static)
        error = self.query_one("#error", Static)
        status.update("Submitting…")
        error.update("")
        success = False
        try:
            try:
                await submit_operator_note(
                    self._client, self._case_id, text, author=self._author
                )
            except (CaseNotFoundError, AnchorVerdictMissingError) as exc:
                error.update(f"Submit failed: {exc}")
                status.update("")
                return
            except CoreUnreachableError:
                error.update(
                    "Submit failed: core unreachable. Note kept — try again."
                )
                status.update("")
                return
            except ReasoningCaptureError as exc:
                error.update(f"Submit failed: {exc}. Note kept — try again.")
                status.update("")
                return
            # Success: clear the input. Refresh fires below outside the
            # finally so the in-flight flag is cleared first — otherwise
            # the immediate refresh's lock check would race with the
            # next operator submit.
            input_widget.value = ""
            status.update("")
            success = True
        finally:
            # Always release the in-flight guard, on success or on any
            # error path. Symmetric with the synchronous set in
            # on_input_submitted so the next submit can proceed.
            self._submit_in_flight = False
        if success:
            await self._refresh()


def _format_note_line(note: OperatorNote) -> str:
    return f"[{note.created_at}] {note.author}: {note.text}"
