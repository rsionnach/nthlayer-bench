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
