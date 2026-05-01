"""Case detail screen — operator's working view of a single case.

Mounts the structured paging brief in the right pane and reserves a
left pane for case context (priority, lease, age, related verdicts).
Reasoning capture lands here in a follow-up under opensrm-81rn.4.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Static

from nthlayer_common.api_client import CoreAPIClient

from nthlayer_bench.widgets.case_brief import CaseBriefPanel


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

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            with Vertical(id="context-pane"):
                yield Static("Case Context", id="context-title", markup=False)
                yield Static(f"ID: {self._case_id}", id="context-id", markup=False)
                # Reasoning capture lands here under a future commit on
                # opensrm-81rn.4. Intentionally absent in this bead — the
                # spec scopes reasoning capture as a separate widget.
                yield Static(
                    "Reasoning capture: coming in a follow-up (opensrm-81rn.4).",
                    id="context-reasoning-placeholder",
                    markup=False,
                )
            yield CaseBriefPanel(self._client, self._case_id)
        yield Footer()
