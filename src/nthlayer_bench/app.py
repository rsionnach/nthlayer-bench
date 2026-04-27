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

    def __init__(self, core_url: str = "http://localhost:8000") -> None:
        super().__init__()
        self.core_url = core_url

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
