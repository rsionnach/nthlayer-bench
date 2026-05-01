"""Case review screen — full-viewport view of the post-incident review.

Pushed from :class:`CaseDetailScreen` via the ``r`` key binding.
Operator returns to case detail with ``escape``. Reuses Bead 3's screen
stack pattern so navigation is symmetric across SRE surfaces.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Header

from nthlayer_common.api_client import CoreAPIClient

from nthlayer_bench.widgets.case_review import CaseReviewPanel


class CaseReviewScreen(Screen):
    """Post-incident review for a single case."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("q", "app.pop_screen", "Back", show=False),
    ]

    def __init__(self, client: CoreAPIClient, case_id: str) -> None:
        """``client`` is the app-shared :class:`CoreAPIClient`; screens
        must not construct their own clients (the app owns the
        connection-pool lifecycle). ``case_id`` is the case to review."""
        super().__init__()
        self._client = client
        self._case_id = case_id

    def compose(self) -> ComposeResult:
        yield Header()
        yield CaseReviewPanel(self._client, self._case_id)
        yield Footer()
