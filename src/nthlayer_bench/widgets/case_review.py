"""Case review panel — renders the post-incident review for a case.

Mounts in the case-review screen (pushed from case-detail via the 'r'
key binding). Polls the same 5s cadence as the brief panel; the only
expected change between polls is when a new outcome_resolution lands.
Inline error states match the brief panel's pattern so the same widget
patterns apply across SRE surfaces.
"""
from __future__ import annotations

import asyncio

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.timer import Timer
from textual.widgets import Static

from nthlayer_common.api_client import CoreAPIClient

from nthlayer_bench.sre.post_incident import (
    AnchorVerdictMissingError,
    CaseNotFoundError,
    CoreUnreachableError,
    PostIncidentError,
    PostIncidentReview,
    build_post_incident_review,
    render_post_incident_review,
)

REFRESH_SECONDS = 5.0


class CaseReviewPanel(VerticalScroll):
    """Right-pane widget showing the live ``PostIncidentReview`` for a case.

    The review is rendered as plain text via ``render_post_incident_review``
    inside a ``markup=False`` Static — verdict text from LLM agents flows
    through the timeline summaries, so Rich-markup parsing is disabled
    end-to-end (same rule as the brief panel).
    """

    DEFAULT_CSS = """
    CaseReviewPanel {
        padding: 1;
        width: 1fr;
        height: 1fr;
    }
    CaseReviewPanel #body { /* Static fills the scroll viewport. */ }
    CaseReviewPanel #error { color: $error; text-style: bold; }
    """

    def __init__(self, client: CoreAPIClient, case_id: str) -> None:
        super().__init__()
        self._client = client
        self._case_id = case_id
        self._refresh_seconds = REFRESH_SECONDS
        self._timer: Timer | None = None
        # Skip-if-locked guard against reentrant refresh: same rationale
        # as CaseBriefPanel — newer data wins by waiting for the next
        # clean tick, no two writers race the Static fields.
        self._refresh_lock = asyncio.Lock()

    def compose(self) -> ComposeResult:
        # markup=False: rendered review embeds verdict summaries from LLM
        # agents and external systems. Rich-markup parsing on untrusted
        # text would let stray `[bold]` reformat the panel.
        yield Static("", id="body", markup=False)
        yield Static("", id="error", markup=False)

    def on_mount(self) -> None:
        self._timer = self.set_interval(self._refresh_seconds, self._refresh)
        self.call_later(self._refresh)

    def on_unmount(self) -> None:
        # Belt-and-braces: explicitly stop the timer so a screen pop
        # doesn't leave a dangling tick attached to a removed widget.
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
            review = await build_post_incident_review(self._client, self._case_id)
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
        except PostIncidentError as exc:
            self._render_error(f"Review unavailable: {exc}")
            return

        self._render_review(review)

    def _render_review(self, review: PostIncidentReview) -> None:
        self._set("error", "")
        self._set("body", render_post_incident_review(review))

    def _render_error(self, message: str) -> None:
        self._set("body", "")
        self._set("error", message)

    def _set(self, widget_id: str, text: str) -> None:
        self.query_one(f"#{widget_id}", Static).update(text)
