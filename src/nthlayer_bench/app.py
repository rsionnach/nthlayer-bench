"""NthLayer Bench Textual application.

Single-screen TUI that connects to core's HTTP API and displays
connection status. Foundation for situation board, case bench,
and case detail screens (Phase 4).
"""

from __future__ import annotations

import asyncio

import httpx
from textual.app import App, ComposeResult
from textual.containers import Container
from textual.widgets import Footer, Header, Static

from nthlayer_common.api_client import CoreAPIClient

from nthlayer_bench.sre.escalation import EscalationEvent, EscalationMonitor

# Notification poller cadence — the operator gets a toast within ~5s of
# a high/critical breach landing on core. Matches the brief/review/bench
# panel cadence so all bench polling is on a single tick budget.
ESCALATION_POLL_SECONDS = 5.0

# Map breach severity to Textual's toast severity (which controls
# colour/persistence). Hoisted to module scope so the mapping is
# greppable in one place — same idiom as the other module-level
# tables in the bench (PRIORITY_ORDER, _CAUSE_PLACEHOLDER, etc).
_SEVERITY_TO_TEXTUAL: dict[str, str] = {
    "critical": "error",     # red, persistent
    "high": "warning",       # orange
}


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
        # Escalation monitor is one-per-session. Owned by the app rather
        # than a screen so the toast surface is reachable from anywhere
        # the operator navigates.
        self._escalation_monitor = EscalationMonitor()
        # Skip-if-locked guard against reentrant polling — same idiom
        # as the panel widgets (CaseBenchPanel, SituationBoardPanel,
        # etc.). On a slow core, the 5s tick can fire while a previous
        # poll is still awaiting; without the lock both would observe
        # the same _seen_ids snapshot and dispatch the same toast twice.
        self._escalation_lock = asyncio.Lock()

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
        # CaseBenchScreen is always the home view — push it first so it
        # sits at the bottom of the screen stack. With --case-id, push
        # CaseDetailScreen on top; pressing escape from there pops back
        # to the bench (not the empty-app default Static), preserving
        # the operator's path back to their queue.
        #
        # Deferred imports: keep screens/ off the import path for headless
        # app construction (tests instantiate BenchApp without ever
        # pushing a screen) and avoid future app↔screens cycles if a
        # screen ever needs to import from app for navigation actions
        # or shared state.
        from nthlayer_bench.screens.case_bench import CaseBenchScreen
        self.push_screen(CaseBenchScreen(self.client))

        # Start the escalation poller. Toasts surface across any screen
        # the operator is on (bench, situation board, case detail,
        # review) — Textual's notify renders at app level, not per-screen.
        self.set_interval(ESCALATION_POLL_SECONDS, self._poll_escalations)
        self.call_later(self._poll_escalations)

        if self._initial_case_id is not None:
            from nthlayer_bench.screens.case_detail import CaseDetailScreen
            self.push_screen(CaseDetailScreen(self.client, self._initial_case_id))

    async def _poll_escalations(self) -> None:
        """Run one escalation poll and dispatch toasts for new events.

        Skip-if-locked guard: a slow core could let the 5s interval
        fire while a previous poll is still in flight. Both polls
        would see the same `_seen_ids` snapshot and dispatch the same
        toast twice. Bail early if another poll holds the lock; the
        next clean tick will pick up newer data.
        """
        if self._escalation_lock.locked():
            return
        async with self._escalation_lock:
            events = await self._escalation_monitor.poll(self.client)
            for event in events:
                self._notify_escalation(event)

    def _notify_escalation(self, event: EscalationEvent) -> None:
        """Render a single escalation event as a Textual toast.

        Title carries service + severity; message carries the verdict
        summary so the operator can decide whether to navigate without
        opening the case. Severity → toast colour mapping lives in
        ``_SEVERITY_TO_TEXTUAL`` at module scope.
        """
        toast_severity = _SEVERITY_TO_TEXTUAL.get(event.severity, "information")
        title = f"{event.severity.upper()}: {event.service}"
        # markup=False on the message — verdict summaries embed text from
        # LLM agents and external systems; Rich-markup parsing on those
        # would let stray brackets reformat the toast.
        self.notify(
            event.summary or "(no summary)",
            title=title,
            severity=toast_severity,
            markup=False,
        )
