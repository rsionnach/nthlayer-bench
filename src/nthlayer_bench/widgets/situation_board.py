"""Situation board panel — system-state-at-a-glance dashboard.

Composes three sections (portfolio counts, recent breach feed, active
queue summary) into one scrollable view. Renders the plain-text output
of :func:`render_situation_board` inside a ``markup=False`` Static —
breach summaries embed verdict text from LLM agents, so Rich-markup
parsing is disabled end-to-end.

Same lifecycle patterns as the other SRE panels: 5s poll, asyncio.Lock
skip-if-locked, on_unmount timer cleanup.
"""
from __future__ import annotations

import asyncio

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.timer import Timer
from textual.widgets import Static

from nthlayer_common.api_client import CoreAPIClient

from nthlayer_bench.sre.situation_board import (
    CoreUnreachableError,
    SituationBoardError,
    SituationBoardView,
    fetch_situation_board,
    render_situation_board,
)

REFRESH_SECONDS = 5.0


class SituationBoardPanel(VerticalScroll):
    """Dashboard panel mounted in :class:`SituationBoardScreen`."""

    DEFAULT_CSS = """
    SituationBoardPanel {
        padding: 1;
        width: 1fr;
        height: 1fr;
    }
    SituationBoardPanel #error { color: $error; text-style: bold; }
    """

    def __init__(self, client: CoreAPIClient) -> None:
        super().__init__()
        self._client = client
        self._refresh_seconds = REFRESH_SECONDS
        self._timer: Timer | None = None
        # Skip-if-locked guard against reentrant refresh — same rationale
        # as the brief, review, and bench panels.
        self._refresh_lock = asyncio.Lock()

    def compose(self) -> ComposeResult:
        # markup=False: the rendered view embeds breach-verdict summaries
        # from LLM agents. Stray Rich markup in those strings would
        # otherwise reformat the dashboard.
        yield Static("", id="body", markup=False)
        yield Static("", id="error", markup=False)

    def on_mount(self) -> None:
        self._timer = self.set_interval(self._refresh_seconds, self._refresh)
        # Immediate first refresh; don't make the operator wait 5s for
        # the dashboard to populate. set_interval fires on the next tick.
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
            view = await fetch_situation_board(self._client)
        except CoreUnreachableError:
            self._render_error("Core unreachable — retrying.")
            return
        except SituationBoardError as exc:
            self._render_error(f"Situation board unavailable: {exc}")
            return
        self._render_view(view)

    def _render_view(self, view: SituationBoardView) -> None:
        self.query_one("#error", Static).update("")
        self.query_one("#body", Static).update(render_situation_board(view))

    def _render_error(self, message: str) -> None:
        self.query_one("#body", Static).update("")
        self.query_one("#error", Static).update(message)
