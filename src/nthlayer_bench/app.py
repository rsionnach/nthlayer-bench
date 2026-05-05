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
from nthlayer_bench.sre.write_queue import WriteQueue

# Notification poller cadence — the operator gets a toast within ~5s of
# a high/critical breach landing on core. Matches the brief/review/bench
# panel cadence so all bench polling is on a single tick budget.
ESCALATION_POLL_SECONDS = 5.0

# Write-queue drain cadence. Same 5s tick — operator gets queued notes
# replayed within one polling cycle of core recovering.
WRITE_QUEUE_DRAIN_SECONDS = 5.0

# Map breach severity to Textual's toast severity (which controls
# colour/persistence). Hoisted to module scope so the mapping is
# greppable in one place — same idiom as the other module-level
# tables in the bench (PRIORITY_ORDER, _CAUSE_PLACEHOLDER, etc).
_SEVERITY_TO_TEXTUAL: dict[str, str] = {
    "critical": "error",     # red, persistent
    "high": "warning",       # orange
}


# Reconnect backoff schedule. On a clean health check we re-poll every
# BASE seconds. On consecutive failures the interval doubles until it
# caps at MAX — so a long core outage doesn't generate one health
# request per 5s for hours, but the operator still gets a status flip
# back to "Connected" within the cap once core returns.
RECONNECT_BASE_INTERVAL = 5.0
RECONNECT_MAX_INTERVAL = 60.0


def _compute_next_interval(consecutive_failures: int) -> float:
    """Pure helper for ``ConnectionStatus``: compute the next health-check
    delay given the current consecutive-failure count.

    failures=0 → BASE (clean state, normal cadence).
    failures≥1 → BASE * 2^failures, capped at MAX.
    Hoisted to module scope so unit tests can assert the full backoff
    schedule (5 → 10 → 20 → 40 → 60 → 60 …) without driving the
    Textual loop on real wall-clock waits.
    """
    if consecutive_failures <= 0:
        return RECONNECT_BASE_INTERVAL
    interval = RECONNECT_BASE_INTERVAL * (2 ** consecutive_failures)
    return min(interval, RECONNECT_MAX_INTERVAL)


class ConnectionStatus(Static):
    """Displays connection status to nthlayer-core.

    Reconnect attempts use exponential backoff: a single failure
    triggers a 10s retry, two failures 20s, then 40s, then capped at
    60s. On a successful check, the failure counter resets and the
    next poll fires at the BASE interval. Implemented via one-shot
    ``set_timer`` rescheduled by each ``_check_health`` invocation
    (Textual's ``set_interval`` is fixed-cadence; we need a dynamic
    interval driven by the result of the previous check).
    """

    CONNECTED = "[green]● Connected[/green]"
    DISCONNECTED = "[red]● Disconnected[/red] — Core unreachable"
    DEGRADED = "[yellow]● Degraded[/yellow] — Data may be stale"

    def __init__(self, core_url: str) -> None:
        super().__init__(self.DISCONNECTED)
        self.core_url = core_url.rstrip("/")
        self._connected = False
        # Consecutive failure counter — drives the backoff schedule via
        # _compute_next_interval. Reset to 0 on every successful check
        # (whether 200 or non-200; only network-level failures count).
        self._consecutive_failures = 0

    def on_mount(self) -> None:
        # Check immediately on mount; subsequent checks reschedule
        # themselves at the end of _check_health based on the result.
        self.call_later(self._check_health)

    async def _check_health(self) -> None:
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self.core_url}/health")
                if resp.status_code == 200:
                    self._connected = True
                    self.update(self.CONNECTED)
                    self._consecutive_failures = 0
                else:
                    self._connected = False
                    self.update(self.DEGRADED)
                    # Non-200 still counts as a reachable core — degraded
                    # rather than disconnected. Keep poll cadence at BASE
                    # so operators see the recovery quickly.
                    self._consecutive_failures = 0
        except (httpx.RequestError, OSError):
            self._connected = False
            self.update(self.DISCONNECTED)
            self._consecutive_failures += 1

        next_seconds = _compute_next_interval(self._consecutive_failures)
        self.set_timer(next_seconds, self._check_health)

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
        # Write queue for operator notes that couldn't be submitted
        # because core was unreachable. The drain timer below replays
        # them; 409 conflict detection drops duplicates cleanly.
        self._write_queue = WriteQueue()

    @property
    def write_queue(self) -> WriteQueue:
        """The app-shared queue of pending operator-note submissions.

        Reasoning-capture panels read this to count pending entries
        for their status line and to enqueue on
        :class:`CoreUnreachableError`. The app drains it on a 5s
        interval via :meth:`_drain_write_queue`.
        """
        return self._write_queue

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
            Static("NthLayer Bench v1.5.0\n\nPhase 4 screens coming soon: Situation Board, Case Bench, Case Detail"),
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

        # Start the write-queue drainer. Operator notes that couldn't
        # be submitted live (core unreachable) replay automatically
        # within one tick of core recovering.
        self.set_interval(WRITE_QUEUE_DRAIN_SECONDS, self._drain_write_queue)

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

    async def _drain_write_queue(self) -> None:
        """Try to replay any queued operator-note submissions.

        WriteQueue.drain owns its own skip-if-locked guard, so a slow
        core won't allow concurrent drains to race the same queue. On
        successful submissions, dispatch a one-line toast so the
        operator sees the recovery confirmation; 409s ("already
        submitted on a prior attempt") drop silently to avoid toast
        spam during the recovery window.
        """
        if len(self._write_queue) == 0:
            return
        result = await self._write_queue.drain(self.client)
        if result.submitted > 0:
            plural = "s" if result.submitted != 1 else ""
            self.notify(
                f"Submitted {result.submitted} pending note{plural}.",
                title="Write queue",
                severity="information",
                markup=False,
            )

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
