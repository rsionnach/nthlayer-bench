"""Case bench panel — operator's queue, priority-grouped, selectable.

Mounts in :class:`CaseBenchScreen` (the bench's default view). Polls
``fetch_case_bench`` every 5s; renders a :class:`ListView` of selectable
case rows interleaved with priority section headers. Selecting a case
posts a :class:`CaseSelected` message that the screen catches to push
:class:`CaseDetailScreen` for that case_id.

Same patterns as :class:`CaseBriefPanel` and :class:`CaseReviewPanel`:
asyncio.Lock skip-if-locked refresh, on_unmount timer cleanup, and
``markup=False`` on data-bearing widgets so LLM-produced briefing text
can't reformat the panel.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.timer import Timer
from textual.widgets import Label, ListItem, ListView, Static

from nthlayer_common.api_client import CoreAPIClient

from nthlayer_bench.sre.case_bench import (
    CaseBenchError,
    CaseBenchView,
    CaseSummary,
    CoreUnreachableError,
    fetch_case_bench,
)

REFRESH_SECONDS = 5.0


class CaseBenchPanel(Vertical):
    """Right-pane widget showing the live operator queue."""

    DEFAULT_CSS = """
    CaseBenchPanel {
        padding: 1;
        width: 1fr;
        height: 1fr;
    }
    CaseBenchPanel #title { text-style: bold; padding-bottom: 1; }
    CaseBenchPanel #status { color: $text-muted; padding-bottom: 1; }
    CaseBenchPanel #error  { color: $error; text-style: bold; }
    CaseBenchPanel ListView {
        height: 1fr;
    }
    CaseBenchPanel ListView > ListItem.priority-header {
        text-style: bold;
        background: $surface;
        padding: 0 1;
    }
    """

    @dataclass
    class CaseSelected(Message):
        """Fired when the operator selects a case row.

        The host screen catches this and pushes the case-detail screen
        for the selected case_id. Carrying the full ``CaseSummary`` lets
        the host pre-populate header text without an extra fetch.
        """

        summary: CaseSummary

    def __init__(self, client: CoreAPIClient, *, refresh_seconds: float = REFRESH_SECONDS) -> None:
        super().__init__()
        self._client = client
        self._refresh_seconds = refresh_seconds
        self._timer: Timer | None = None
        # Skip-if-locked guard against reentrant refresh — same rationale
        # as the brief and review panels.
        self._refresh_lock = asyncio.Lock()
        # Map ListItem id → CaseSummary so selection events can resolve
        # the case being clicked. Rebuilt on each refresh.
        self._items_by_id: dict[str, CaseSummary] = {}

    def compose(self) -> ComposeResult:
        yield Static("Active cases", id="title", markup=False)
        yield Static("", id="status", markup=False)
        yield Static("", id="error", markup=False)
        yield ListView(id="case-list")

    def on_mount(self) -> None:
        self._timer = self.set_interval(self._refresh_seconds, self._refresh)
        # Immediate first refresh; don't make the operator wait 5s to see
        # the queue. set_interval fires on the *next* tick.
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
            view = await fetch_case_bench(self._client)
        except CoreUnreachableError:
            self._render_error("Core unreachable — retrying.")
            return
        except CaseBenchError as exc:
            self._render_error(f"Bench unavailable: {exc}")
            return
        await self._render_view(view)

    async def _render_view(self, view: CaseBenchView) -> None:
        self.query_one("#error", Static).update("")
        if not view.flat:
            self.query_one("#status", Static).update("No active cases.")
        else:
            counts = ", ".join(
                f"{p}: {len(view.cases_by_priority[p])}" for p in view.ordered_priorities
            )
            self.query_one("#status", Static).update(counts)

        list_view = self.query_one("#case-list", ListView)
        await list_view.clear()
        self._items_by_id.clear()

        for priority in view.ordered_priorities:
            header_id = f"hdr-{priority}"
            await list_view.append(
                ListItem(
                    Label(f"{priority}", markup=False),
                    id=header_id,
                    classes="priority-header",
                )
            )
            for case in view.cases_by_priority[priority]:
                item_id = f"case-{case.case_id}"
                self._items_by_id[item_id] = case
                await list_view.append(
                    ListItem(
                        Label(_format_row(case), markup=False),
                        id=item_id,
                    )
                )

    def _render_error(self, message: str) -> None:
        self.query_one("#status", Static).update("")
        self.query_one("#error", Static).update(message)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Translate the ListView selection into a CaseSelected message.

        Priority-header rows are not selectable cases — guard against the
        operator landing on one (the ListView still moves the cursor to
        them). Skip silently rather than posting a header as a case.
        """
        item_id = event.item.id if event.item else None
        if item_id is None:
            return
        summary = self._items_by_id.get(item_id)
        if summary is None:
            return  # priority header or stale item
        self.post_message(self.CaseSelected(summary=summary))


def _format_row(case: CaseSummary) -> str:
    age = f"{case.age_minutes}m" if case.age_minutes is not None else "—"
    briefing = f" — {case.briefing}" if case.briefing else ""
    return f"  {case.case_id}  {case.service}  [{case.state}]  age={age}{briefing}"
