"""Situation board screen — operator's dashboard view.

Pushed from :class:`CaseBenchScreen` via the ``s`` key binding. Operator
returns to the bench (queue) with ``escape``. Screen-stack pattern
matches case-detail / case-review (Beads 3+4) so navigation is symmetric
across SRE surfaces.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Header

from nthlayer_common.api_client import CoreAPIClient

from nthlayer_bench.widgets.situation_board import SituationBoardPanel


class SituationBoardScreen(Screen):
    """System-state-at-a-glance dashboard."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("q", "app.pop_screen", "Back", show=False),
    ]

    def __init__(self, client: CoreAPIClient) -> None:
        """``client`` is the app-shared :class:`CoreAPIClient`; screens
        must not construct their own clients (the app owns the
        connection-pool lifecycle)."""
        super().__init__()
        self._client = client

    def compose(self) -> ComposeResult:
        yield Header()
        yield SituationBoardPanel(self._client)
        yield Footer()
