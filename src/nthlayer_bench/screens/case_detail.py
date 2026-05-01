"""Case detail screen — operator's working view of a single case.

Two-pane layout:

- **Left context pane (36 cols)** — case ID and the live reasoning
  capture panel (operator notes feed + input).
- **Right pane (1fr)** — the paging brief (severity, summary, likely
  cause, blast radius, recommended action) with state-aware refresh.

Reachable directly via ``--case-id`` (BenchApp pushes this screen on
mount) or by selecting a case in the case bench. Press ``r`` for the
post-incident review; ``escape`` returns to the previous screen.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from nthlayer_common.api_client import CoreAPIClient

from nthlayer_bench.widgets.case_brief import CaseBriefPanel
from nthlayer_bench.widgets.reasoning_capture import ReasoningCapturePanel


class CaseDetailScreen(Screen):
    """Full-viewport view of a single case for the operator on bench."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("q", "app.pop_screen", "Back", show=False),
        Binding("r", "open_review", "Review"),
    ]

    def action_open_review(self) -> None:
        """Push the post-incident review screen for this case."""
        # Deferred import: same rationale as BenchApp.on_mount — keeps
        # the screens/ subpackage out of the import path for headless
        # test construction and avoids future detail↔review cycles.
        from nthlayer_bench.screens.case_review import CaseReviewScreen
        self.app.push_screen(CaseReviewScreen(self._client, self._case_id))

    # 36 cols on the context pane fits ID + priority badge + lease
    # countdown comfortably at the standard 80-col terminal layout, leaving
    # 44+ cols for the brief on the right. Adjust together if either pane
    # grows new fields.
    DEFAULT_CSS = """
    CaseDetailScreen {
        layout: vertical;
    }
    CaseDetailScreen #body {
        height: 1fr;
    }
    CaseDetailScreen #context-pane {
        width: 36;
        border-right: solid $primary;
        padding: 1;
    }
    CaseDetailScreen #context-title {
        text-style: bold;
    }
    """

    def __init__(self, client: CoreAPIClient, case_id: str) -> None:
        """``client`` is the app-shared :class:`CoreAPIClient` (from
        ``BenchApp.client``); screens must not construct their own clients
        — the app owns the connection-pool lifecycle. ``case_id`` is the
        case to display."""
        super().__init__()
        self._client = client
        self._case_id = case_id

    @property
    def case_id(self) -> str:
        """Public accessor — used by the case bench's double-push guard
        to compare the topmost screen's case against an incoming
        selection without reaching into private attrs."""
        return self._case_id

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            with Vertical(id="context-pane"):
                yield Static("Case Context", id="context-title", markup=False)
                yield Static(f"ID: {self._case_id}", id="context-id", markup=False)
                # Pass the app-shared write queue so the panel can
                # enqueue offline-typed notes (Bead 9b). Falls back to
                # legacy "keep input on error" behaviour if the app
                # doesn't expose one.
                write_queue = getattr(self.app, "write_queue", None)
                yield ReasoningCapturePanel(
                    self._client, self._case_id, write_queue=write_queue
                )
            yield CaseBriefPanel(self._client, self._case_id)
        yield Footer()
