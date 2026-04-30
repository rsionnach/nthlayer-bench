"""NthLayer Bench Textual application.

Single-screen TUI that connects to core's HTTP API and displays
connection status. Foundation for situation board, case bench,
and case detail screens (Phase 4).
"""

from __future__ import annotations

import httpx
from textual.app import App, ComposeResult
from textual.containers import Container
from textual.widgets import Footer, Header, Static

from nthlayer_common.api_client import CoreAPIClient


class ConnectionStatus(Static):
    """Displays connection status to nthlayer-core."""

    CONNECTED = "[green]● Connected[/green]"
    DISCONNECTED = "[red]● Disconnected[/red] — Core unreachable"
    DEGRADED = "[yellow]● Degraded[/yellow] — Data may be stale"

    def __init__(self, core_url: str) -> None:
        super().__init__(self.DISCONNECTED)
        self.core_url = core_url.rstrip("/")
        self._connected = False

    def on_mount(self) -> None:
        self.set_interval(5.0, self._check_health)
        # Check immediately on mount
        self.call_later(self._check_health)

    async def _check_health(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.core_url}/health")
                if resp.status_code == 200:
                    self._connected = True
                    self.update(self.CONNECTED)
                else:
                    self._connected = False
                    self.update(self.DEGRADED)
        except (httpx.RequestError, OSError):
            self._connected = False
            self.update(self.DISCONNECTED)

    @property
    def is_connected(self) -> bool:
        return self._connected


class BenchApp(App):
    """NthLayer Bench — operator terminal UI."""

    TITLE = "NthLayer Bench"
    CSS = """
    #status-bar {
        dock: top;
        height: 3;
        padding: 1;
        background: $surface;
    }
    #main {
        padding: 1;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
    ]

    def __init__(
        self,
        core_url: str = "http://localhost:8000",
        *,
        initial_case_id: str | None = None,
    ) -> None:
        super().__init__()
        self.core_url = core_url
        self._initial_case_id = initial_case_id
        # CoreAPIClient is async-managed; instantiated lazily so screens
        # share a single client instance per app run.
        self._client: CoreAPIClient | None = None

    @property
    def client(self) -> CoreAPIClient:
        """Lazily-instantiated shared CoreAPIClient for child screens.

        Single instance per app run — screens that need core access pull
        from here rather than constructing their own clients, so the
        underlying httpx connection pool is shared and the lifecycle is
        the app's. Closed in :meth:`_on_exit_app`.
        """
        if self._client is None:
            self._client = CoreAPIClient(base_url=self.core_url)
        return self._client

    async def _on_exit_app(self) -> None:
        # Close the shared CoreAPIClient on app shutdown so the underlying
        # httpx connection pool isn't leaked. The client is lazy — only
        # close if it was actually instantiated. _on_exit_app is Textual's
        # cleanup hook fired during App.exit().
        #
        # try/finally chain: if close() raises (e.g. transport already
        # half-closed by a server hang-up), Textual's super()._on_exit_app
        # MUST still run so its message-loop teardown isn't stranded.
        try:
            if self._client is not None:
                await self._client.close()
        finally:
            self._client = None
            await super()._on_exit_app()

    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            ConnectionStatus(self.core_url),
            id="status-bar",
        )
        yield Container(
            Static("NthLayer Bench v1.5.0a1\n\nPhase 4 screens coming soon: Situation Board, Case Bench, Case Detail"),
            id="main",
        )
        yield Footer()

    def on_mount(self) -> None:
        # If launched with --case-id, drop the operator straight onto the
        # case detail screen. Useful for paging links and direct demos
        # before the situation board / case bench land.
        if self._initial_case_id is not None:
            # Deferred import: keeps screens/ off the import path for
            # headless app construction (tests instantiate BenchApp without
            # ever pushing a screen) and avoids future app↔screens cycles
            # if a screen ever needs to import from app for navigation
            # actions or shared state.
            from nthlayer_bench.screens.case_detail import CaseDetailScreen
            self.push_screen(CaseDetailScreen(self.client, self._initial_case_id))
