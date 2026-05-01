"""Shared pytest fixtures for the bench test suite.

The autouse ``_quiet_escalation_monitor`` fixture patches
:meth:`EscalationMonitor.poll` to a no-op for every test except the
dedicated escalation tests in ``test_sre_escalation.py``. The poller
runs on every ``BenchApp.run_test`` and most tests don't exercise it;
without this patch each test pays for an unmocked
``client.get_verdicts(verdict_type=...)`` round-trip via the AsyncMock
chain, which roughly doubled the suite runtime when escalation landed.

Tests that explicitly exercise the escalation flow override the patch
locally with ``patch.object(app._escalation_monitor, "poll", new=...)``
inside their own context, as ``test_app.py`` does for the
``test_app_dispatches_toast_on_new_escalation`` family.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _quiet_escalation_monitor(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    if request.node.path.name == "test_sre_escalation.py":
        return

    from nthlayer_bench.sre.escalation import EscalationMonitor

    async def _noop(self, client):
        return []

    monkeypatch.setattr(EscalationMonitor, "poll", _noop)
