"""Case bench screen — the operator's home view.

Pushed by :class:`BenchApp` on mount when the bench launches without
``--case-id``. Selecting a case in the panel pushes
:class:`CaseDetailScreen` for that case_id; from there the operator can
open the post-incident review with ``r``.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Header

from nthlayer_common.api_client import CoreAPIClient

from nthlayer_bench.widgets.case_bench import CaseBenchPanel


class CaseBenchScreen(Screen):
    """Operator queue: priority-grouped list of active cases."""

    # The bench is the home view — no "q → pop_screen" binding (that
    # would land the operator on the empty-app Static). The app-level
    # "q → quit" binding is what 'q' should do here. The 's' binding
    # pushes the situation board (system dashboard); operator pops back
    # to the bench from there with escape.
    BINDINGS = [
        Binding("s", "open_situation_board", "Situation board"),
    ]

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

    def action_open_situation_board(self) -> None:
        """Push the situation-board screen on top of the bench."""
        # Deferred import: same rationale as the case-detail push from
        # the bench panel — keeps screens/ off the import path for
        # headless app construction and avoids future bench↔dashboard
        # cycles.
        from nthlayer_bench.screens.situation_board import SituationBoardScreen
        self.app.push_screen(SituationBoardScreen(self._client))
