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
    build_operator_note_verdict,
    fetch_case,
    fetch_operator_notes,
    submit_operator_note,
)
from nthlayer_bench.sre.write_queue import WriteQueue

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
        write_queue: WriteQueue | None = None,
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
        # Most-recent successful case fetch. Cached so the panel can
        # build operator-note verdicts for the offline write queue
        # without a fresh fetch (which would itself fail when core is
        # the reason we're queuing). None until the first successful
        # refresh — first-submit-before-first-refresh falls back to
        # the live submit_operator_note path.
        self._cached_case: dict | None = None
        # Optional app-shared write queue. When None, submit failures
        # surface inline errors only (legacy Bead 7 behaviour).
        self._write_queue = write_queue

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
            # Fetch case + notes together. The case dict is cached for
            # the offline write-queue path; if core goes down between
            # this refresh and the next submit, the panel can still
            # build a valid operator-note verdict from the cache.
            case = await fetch_case(self._client, self._case_id)
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
        self._cached_case = case
        await self._render_notes(notes)
        self._refresh_pending_status()

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
                # Bead 9b: instead of leaving the operator to retry by
                # hand, build the verdict from the cached case and
                # enqueue. The app-level drain timer will replay it
                # when core returns.
                queued = self._enqueue_for_retry(text, error, status)
                if queued:
                    input_widget.value = ""
                    self._refresh_pending_status()
                # If we couldn't queue (no cached case yet), the inline
                # error above stays; operator can retry manually.
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

    def _enqueue_for_retry(
        self, text: str, error: Static, status: Static
    ) -> bool:
        """Build a verdict from the cached case and enqueue it on the
        app's write queue. Returns True if queued, False if the panel
        couldn't build (no cached case yet, or build raised).
        """
        if self._write_queue is None or self._cached_case is None:
            error.update(
                "Submit failed: core unreachable. Note kept — try again."
            )
            status.update("")
            return False
        try:
            verdict = build_operator_note_verdict(
                self._cached_case, text, author=self._author
            )
        except (ValueError, AnchorVerdictMissingError) as exc:
            error.update(f"Submit failed: {exc}")
            status.update("")
            return False
        self._write_queue.enqueue(verdict, self._case_id)
        # Status update is delegated to _refresh_pending_status() — the
        # caller (_do_submit) invokes it after this returns True. That
        # keeps "Pending submission(s): N" the single source of operator-
        # visible queue state; we don't write a competing message here.
        error.update("")
        return True

    def _refresh_pending_status(self) -> None:
        """Update the status line with the current queued-write count.

        Called after each refresh and each successful enqueue. When the
        queue is empty (drain succeeded or no queue configured), status
        clears. Operator sees a live "N pending" counter so they know
        not to manually retype a note that's already queued.
        """
        if self._write_queue is None:
            return
        pending = len(self._write_queue)
        status = self.query_one("#status", Static)
        if pending == 0:
            # Only clear when the current message was set by *this*
            # method (i.e. starts with "Pending"). Other writers may
            # have set a transient status like "Submitting…" that we
            # shouldn't wipe — they own their own clear-on-completion.
            # Coupled to the invariant that all writers in this widget
            # call status.update() with plain strings (no Rich
            # renderables), so str(content) round-trips reliably.
            current = str(status.content)
            if current.startswith("Pending"):
                status.update("")
        else:
            plural = "s" if pending != 1 else ""
            status.update(f"Pending submission{plural}: {pending}")


def _format_note_line(note: OperatorNote) -> str:
    return f"[{note.created_at}] {note.author}: {note.text}"
