"""Case bench screen — the operator's home view.

Pushed by :class:`BenchApp` on mount when the bench launches without
``--case-id``. Selecting a case in the panel pushes
:class:`CaseDetailScreen` for that case_id; from there the operator can
open the post-incident review with ``r``.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Footer, Header

from nthlayer_common.api_client import CoreAPIClient

from nthlayer_bench.widgets.case_bench import CaseBenchPanel


class CaseBenchScreen(Screen):
    """Operator queue: priority-grouped list of active cases."""

    # No screen-level bindings: the bench is the home view, so the
    # app-level "q → quit" binding (BenchApp.BINDINGS) is what the
    # operator should hit. A screen-level "q → pop_screen" would pop
    # the home view to the empty-app default Static, which isn't a
    # useful destination.
    BINDINGS: list = []

    def __init__(self, client: CoreAPIClient) -> None:
        """``client`` is the app-shared :class:`CoreAPIClient` (from
        ``BenchApp.client``); screens must not construct their own clients
        — the app owns the connection-pool lifecycle."""
        super().__init__()
        self._client = client

    def compose(self) -> ComposeResult:
        yield Header()
        yield CaseBenchPanel(self._client)
        yield Footer()

    def on_case_bench_panel_case_selected(
        self, event: CaseBenchPanel.CaseSelected
    ) -> None:
        """Operator picked a case — push case-detail for that case_id.

        Guard against double-push when the topmost screen is already a
        case-detail for the same case (operator mashing enter on the
        same row): skip the second push so the screen stack doesn't
        accumulate duplicates."""
        # Deferred import: same rationale as BenchApp.on_mount — keeps
        # screens/ off the import path for headless test construction
        # and avoids future bench↔detail cycles.
        from nthlayer_bench.screens.case_detail import CaseDetailScreen

        case_id = event.summary.case_id
        top = self.app.screen
        if isinstance(top, CaseDetailScreen) and top.case_id == case_id:
            return
        self.app.push_screen(CaseDetailScreen(self._client, case_id))
