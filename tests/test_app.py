"""Tests for the Bench Textual application."""

import pytest
from unittest.mock import AsyncMock, patch

from nthlayer_bench.app import BenchApp, ConnectionStatus


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

    async def test_app_exit_without_client_does_not_crash(self):
        """If the app exits without ever instantiating the client (no
        screens that needed core access), _on_exit_app must handle the
        None gracefully rather than raising."""
        app = BenchApp(core_url="http://test:8000")
        async with app.run_test():
            assert app._client is None
            await app._on_exit_app()  # must not raise

    async def test_app_pushes_case_detail_screen_when_initial_case_id_set(self):
        """End-to-end --case-id behavior: on mount, the app pushes a
        CaseDetailScreen wired to the configured case_id."""
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
