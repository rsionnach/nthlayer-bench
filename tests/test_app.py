"""Tests for the Bench Textual application."""

import contextlib

import pytest
from unittest.mock import AsyncMock, patch

from nthlayer_bench.app import BenchApp, ConnectionStatus
from nthlayer_bench.sre.case_bench import CaseBenchView


@contextlib.contextmanager
def _empty_case_bench():
    """Patch fetch_case_bench to return an empty view so app-lifecycle
    tests don't crash on the case-bench panel's auto-poll.

    BenchApp pushes CaseBenchScreen on mount when no --case-id is set;
    that screen mounts CaseBenchPanel which calls fetch_case_bench on
    its first refresh. Tests that aren't exercising the case-bench
    code path inject an empty result here so the panel renders
    'No active cases.' and stays out of the way."""
    with patch(
        "nthlayer_bench.widgets.case_bench.fetch_case_bench",
        new=AsyncMock(return_value=CaseBenchView()),
    ):
        yield


class TestConnectionStatus:
    def test_initial_state_disconnected(self):
        cs = ConnectionStatus("http://localhost:8000")
        assert not cs.is_connected

    async def test_connected_on_healthy_core(self):
        cs = ConnectionStatus("http://test:8000")

        mock_response = AsyncMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("nthlayer_bench.app.httpx.AsyncClient", return_value=mock_client):
            await cs._check_health()

        assert cs.is_connected

    async def test_degraded_on_non_200_response(self):
        cs = ConnectionStatus("http://test:8000")

        mock_response = AsyncMock()
        mock_response.status_code = 503

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("nthlayer_bench.app.httpx.AsyncClient", return_value=mock_client):
            await cs._check_health()

        assert not cs.is_connected

    async def test_disconnected_on_connection_error(self):
        cs = ConnectionStatus("http://test:8000")

        import httpx
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))

        with patch("nthlayer_bench.app.httpx.AsyncClient", return_value=mock_client):
            await cs._check_health()

        assert not cs.is_connected


class TestBenchApp:
    def test_app_creates(self):
        app = BenchApp(core_url="http://test:8000")
        assert app.core_url == "http://test:8000"
        assert app.TITLE == "NthLayer Bench"

    def test_app_creates_with_no_initial_case_id(self):
        """Default behavior: launching the bench without --case-id leaves
        the app on its default screen (no auto-push). Backward compatible
        with the bench skeleton (opensrm-81rn.1)."""
        app = BenchApp(core_url="http://test:8000")
        assert app._initial_case_id is None

    def test_app_stores_initial_case_id(self):
        """--case-id passes through to the app constructor and is held
        for on_mount to push the case-detail screen."""
        app = BenchApp(core_url="http://test:8000", initial_case_id="case-XYZ")
        assert app._initial_case_id == "case-XYZ"

    async def test_app_closes_client_on_exit(self):
        """Resource cleanup: the shared CoreAPIClient must be closed on app
        shutdown so the underlying httpx connection pool isn't leaked
        across operator sessions."""
        from nthlayer_common.api_client import CoreAPIClient

        app = BenchApp(core_url="http://test:8000")

        with _empty_case_bench():
            async with app.run_test() as pilot:
                # Touch the property so the lazy client gets instantiated.
                client = app.client
                assert isinstance(client, CoreAPIClient)
                assert app._client is client

                with patch.object(client, "close", new=AsyncMock()) as close_mock:
                    await app._on_exit_app()
                    close_mock.assert_awaited_once()

                # Closed client is cleared so a re-entered loop wouldn't
                # touch a stale connection pool.
                assert app._client is None

    async def test_app_exit_app_chains_super_even_when_close_raises(self):
        """If client.close() raises (transport half-closed by a server
        hang-up), Textual's super()._on_exit_app must still run so the
        message-loop teardown isn't stranded. The client is also cleared
        so the next exit attempt sees None."""
        from nthlayer_common.api_client import CoreAPIClient

        app = BenchApp(core_url="http://test:8000")
        with _empty_case_bench():
            async with app.run_test():
                client = app.client
                assert isinstance(client, CoreAPIClient)

                with patch.object(
                    client, "close", new=AsyncMock(side_effect=RuntimeError("transport gone"))
                ):
                    with pytest.raises(RuntimeError, match="transport gone"):
                        # super()._on_exit_app() runs in the finally block, but
                        # the original close-raise still propagates after.
                        await app._on_exit_app()

                assert app._client is None  # cleared in the finally branch

    async def test_app_exit_does_not_crash_when_client_is_none(self):
        """If _client is None at exit time (manually cleared, never
        instantiated, or already closed), _on_exit_app must handle it
        gracefully — None-guard in the finally branch."""
        app = BenchApp(core_url="http://test:8000")
        with _empty_case_bench():
            async with app.run_test():
                # Force-clear so we can exercise the None branch even
                # though the case-bench panel touched the client at mount.
                app._client = None
                await app._on_exit_app()  # must not raise

    async def test_app_pushes_case_detail_screen_when_initial_case_id_set(self):
        """End-to-end --case-id behavior: on mount, the app pushes a
        CaseDetailScreen wired to the configured case_id (NOT the case
        bench)."""
        from nthlayer_bench.screens.case_detail import CaseDetailScreen
        from nthlayer_bench.sre.brief import PagingBrief

        brief = PagingBrief(
            case_id="case-XYZ",
            service="fraud-detect",
            severity=2,
            summary="s",
            state="triage_complete",
            awaiting=["correlation", "remediation"],
        )
        app = BenchApp(core_url="http://test:8000", initial_case_id="case-XYZ")
        with patch(
            "nthlayer_bench.widgets.case_brief.build_paging_brief",
            new=AsyncMock(return_value=brief),
        ):
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.pause()
                assert isinstance(app.screen, CaseDetailScreen)
                assert app.screen._case_id == "case-XYZ"

    async def test_app_pushes_case_bench_screen_when_no_initial_case_id(self):
        """Default behavior: bench launches without --case-id → operator
        lands on CaseBenchScreen (the queue) as the home view."""
        from nthlayer_bench.screens.case_bench import CaseBenchScreen

        app = BenchApp(core_url="http://test:8000")
        with _empty_case_bench():
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.pause()
                assert isinstance(app.screen, CaseBenchScreen)

    async def test_app_dispatches_toast_on_new_escalation(self):
        """When EscalationMonitor.poll yields a new event, BenchApp's
        notify is called with severity-mapped Textual toast colour and
        markup-disabled message body."""
        from nthlayer_bench.sre.escalation import EscalationEvent

        app = BenchApp(core_url="http://test:8000")
        # Pre-mark baseline as done so the first poll's events fire.
        app._escalation_monitor._baseline_done = True

        events = [
            EscalationEvent(
                verdict_id="vrd-1",
                service="fraud-detect",
                severity="critical",
                summary="reversal rate at 8%",
                created_at="2026-04-30T10:25:00Z",
            )
        ]

        notify_calls = []

        def fake_notify(message, *, title=None, severity=None, markup=None, **kwargs):
            notify_calls.append({
                "message": message, "title": title,
                "severity": severity, "markup": markup,
            })

        with _empty_case_bench(), patch.object(
            app._escalation_monitor, "poll",
            new=AsyncMock(return_value=events),
        ):
            async with app.run_test() as pilot:
                with patch.object(app, "notify", side_effect=fake_notify):
                    await app._poll_escalations()
                await pilot.pause()

        assert len(notify_calls) == 1
        call = notify_calls[0]
        assert "reversal rate at 8%" in call["message"]
        assert "CRITICAL" in (call["title"] or "")
        assert "fraud-detect" in (call["title"] or "")
        assert call["severity"] == "error"  # critical → error (red)
        assert call["markup"] is False

    async def test_app_dispatches_warning_severity_for_high_breaches(self):
        """High-severity breaches map to Textual's 'warning' (orange)
        rather than 'error' (red) so the operator can distinguish
        critical from high at a glance."""
        from nthlayer_bench.sre.escalation import EscalationEvent

        app = BenchApp(core_url="http://test:8000")
        app._escalation_monitor._baseline_done = True

        events = [
            EscalationEvent(
                verdict_id="vrd-2",
                service="fraud-detect",
                severity="high",
                summary="latency drift",
                created_at="2026-04-30T10:25:00Z",
            )
        ]

        notify_calls = []

        def fake_notify(message, *, title=None, severity=None, markup=None, **kwargs):
            notify_calls.append({"severity": severity, "title": title})

        with _empty_case_bench(), patch.object(
            app._escalation_monitor, "poll",
            new=AsyncMock(return_value=events),
        ):
            async with app.run_test() as pilot:
                with patch.object(app, "notify", side_effect=fake_notify):
                    await app._poll_escalations()
                await pilot.pause()

        assert notify_calls[0]["severity"] == "warning"
        assert "HIGH" in notify_calls[0]["title"]

    async def test_app_concurrent_polls_dispatch_toast_only_once(self):
        """Skip-if-locked guard: if the 5s tick fires while a previous
        poll is still in flight (slow core), the second invocation
        must not run — otherwise both would observe the same
        ``_seen_ids`` snapshot and dispatch the same toast twice.

        Tested at the lock-semantics level — exercises ``_poll_escalations``
        directly without mounting the full Textual app, since the
        invariant being asserted is about the BenchApp's own lock and
        notify dispatch, not about screen rendering."""
        import asyncio

        from nthlayer_bench.sre.escalation import EscalationEvent

        app = BenchApp(core_url="http://test:8000")
        app._escalation_monitor._baseline_done = True

        gate = asyncio.Event()
        call_count = {"polls": 0}

        async def slow_poll(client):
            call_count["polls"] += 1
            await gate.wait()
            return [
                EscalationEvent(
                    verdict_id="vrd-X",
                    service="fraud-detect",
                    severity="critical",
                    summary="x",
                    created_at="2026-04-30T10:00:00Z",
                )
            ]

        notify_calls = []

        def fake_notify(*args, **kwargs):
            notify_calls.append(args)

        app._escalation_monitor.poll = slow_poll  # type: ignore[method-assign]
        app.notify = fake_notify  # type: ignore[method-assign]

        first = asyncio.create_task(app._poll_escalations())
        await asyncio.sleep(0)  # let `first` enter and acquire the lock
        # Second invocation arrives while first is parked on gate → bails
        # via the locked guard without calling poll.
        await app._poll_escalations()
        assert call_count["polls"] == 1, "Second poll must not invoke poll()"

        gate.set()
        await first

        # Despite two scheduled polls, only one toast was dispatched.
        assert len(notify_calls) == 1

    async def test_app_skips_notification_when_no_new_events(self):
        """No-op cycle: poll returns empty list → no notify calls. The
        toast surface stays quiet so operators only hear about real
        new events."""
        app = BenchApp(core_url="http://test:8000")
        notify_calls = []

        def fake_notify(*args, **kwargs):
            notify_calls.append(args)

        with _empty_case_bench(), patch.object(
            app._escalation_monitor, "poll",
            new=AsyncMock(return_value=[]),
        ):
            async with app.run_test() as pilot:
                with patch.object(app, "notify", side_effect=fake_notify):
                    await app._poll_escalations()
                await pilot.pause()

        assert notify_calls == []

    async def test_deep_link_pop_returns_to_case_bench(self):
        """Launch with --case-id (deep-link from a paging URL), pop the
        case-detail screen → operator lands on the case bench, not the
        app's empty placeholder. CaseBenchScreen is pushed FIRST so the
        bench is always at the bottom of the stack."""
        from nthlayer_bench.screens.case_bench import CaseBenchScreen
        from nthlayer_bench.screens.case_detail import CaseDetailScreen
        from nthlayer_bench.sre.brief import PagingBrief

        brief = PagingBrief(
            case_id="case-XYZ",
            service="fraud-detect",
            severity=2,
            summary="s",
            state="triage_complete",
            awaiting=["correlation", "remediation"],
        )
        app = BenchApp(core_url="http://test:8000", initial_case_id="case-XYZ")
        with _empty_case_bench(), patch(
            "nthlayer_bench.widgets.case_brief.build_paging_brief",
            new=AsyncMock(return_value=brief),
        ):
            async with app.run_test() as pilot:
                await pilot.pause()
                await pilot.pause()
                assert isinstance(app.screen, CaseDetailScreen)

                await app.pop_screen()
                await pilot.pause()
                assert isinstance(app.screen, CaseBenchScreen)
