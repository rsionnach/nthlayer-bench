"""Case-detail right pane: state-aware paging brief.

Mounts in the case-detail screen, polls every 5s, and renders the
current ``PagingBrief`` for the selected case using Textual primitives.
Errors (case missing, anchor missing, core unreachable) render inline
rather than crashing the bench app.
"""
from __future__ import annotations

import asyncio

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.timer import Timer
from textual.widgets import Static

from nthlayer_common.api_client import CoreAPIClient

from nthlayer_bench.sre.brief import (
    AnchorVerdictMissingError,
    BriefError,
    BriefState,
    CaseNotFoundError,
    CoreUnreachableError,
    PagingBrief,
    build_paging_brief,
)

REFRESH_SECONDS = 5.0

# Placeholder strings for the "cause" line, keyed by lifecycle state.
# Other lines (status, recommended) inline their state-specific strings
# directly in _render_brief — they don't share a uniform shape with cause.
_CAUSE_PLACEHOLDER: dict[BriefState, str] = {
    "minimal": "Awaiting triage…",
    "triage_complete": "Investigation in progress",
    "investigation_complete": "Awaiting remediation proposal",
    "remediation_proposed": "",
}


class CaseBriefPanel(Vertical):
    """Right-pane widget showing the live ``PagingBrief`` for a case.

    Refreshes every ``REFRESH_SECONDS`` (5s) regardless of case state.
    Terminal cases (resolved, escalated) don't generate new verdicts so
    polling is wasteful but harmless at v1.5 scale (see spec §Textual
    widget). v2 should consider backing off on terminal cases.
    """

    DEFAULT_CSS = """
    CaseBriefPanel {
        padding: 1;
        width: 1fr;
        height: 1fr;
    }
    CaseBriefPanel #header { text-style: bold; }
    CaseBriefPanel #status { color: $text-muted; }
    CaseBriefPanel #error  { color: $error; text-style: bold; }
    """

    def __init__(self, client: CoreAPIClient, case_id: str) -> None:
        super().__init__()
        self._client = client
        self._case_id = case_id
        self._refresh_seconds = REFRESH_SECONDS
        self._timer: Timer | None = None
        # Skip-if-locked guard against reentrant refresh: if a previous
        # _refresh is still in flight when the next interval tick fires
        # (slow core, large descendant chain), drop the new tick rather
        # than racing two writers on the widget's Static fields. Newer
        # data wins by waiting for the next clean tick.
        self._refresh_lock = asyncio.Lock()

    def compose(self) -> ComposeResult:
        # markup=False: verdict text (subject.summary, judgment.reasoning,
        # metadata.custom values) flows through these fields and originates
        # from LLM agents and external systems. Rich-markup parsing on
        # untrusted strings would let stray `[bold]` reformat the panel or
        # raise on malformed brackets. Styling lives in CSS, not markup, so
        # disabling parsing costs nothing here.
        yield Static("", id="header", markup=False)
        yield Static("", id="status", markup=False)
        yield Static("", id="summary", markup=False)
        yield Static("", id="cause", markup=False)
        yield Static("", id="blast", markup=False)
        yield Static("", id="recommended", markup=False)
        yield Static("", id="error", markup=False)

    def on_mount(self) -> None:
        self._timer = self.set_interval(self._refresh_seconds, self._refresh)
        self.call_later(self._refresh)

    def on_unmount(self) -> None:
        # Spec line 201: clear the interval when the user navigates away.
        # Textual auto-cancels timers attached to removed widgets in
        # practice, but stopping explicitly is belt-and-braces and
        # unambiguous in tests.
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    async def _refresh(self) -> None:
        if self._refresh_lock.locked():
            # Previous refresh still in flight; skip this tick. The next
            # tick will pick up newer data once the in-flight one returns.
            return
        async with self._refresh_lock:
            await self._do_refresh()

    async def _do_refresh(self) -> None:
        try:
            brief = await build_paging_brief(self._client, self._case_id)
        except CaseNotFoundError:
            self._render_error(f"Case {self._case_id!r} not found.")
            return
        except AnchorVerdictMissingError as exc:
            self._render_error(
                f"Data integrity issue: anchor verdict {exc} not found."
            )
            return
        except CoreUnreachableError:
            self._render_error("Core unreachable — retrying.")
            return
        except BriefError as exc:
            self._render_error(f"Brief unavailable: {exc}")
            return

        self._render_brief(brief)

    def _render_brief(self, brief: PagingBrief) -> None:
        self._set("error", "")

        if brief.severity is None:
            self._set("header", f"Severity: unknown — {brief.service}")
        else:
            label = f"P{brief.severity}"
            self._set("header", f"{label}: {brief.service}")

        if brief.state == "remediation_proposed":
            self._set("status", "")
        elif brief.awaiting:
            self._set(
                "status",
                f"Status: {brief.state} (awaiting: {', '.join(brief.awaiting)})",
            )
        else:
            self._set("status", f"Status: {brief.state}")

        self._set("summary", f"What's happening: {brief.summary}")

        placeholder = _CAUSE_PLACEHOLDER.get(brief.state, "")
        if brief.likely_cause:
            cause = brief.likely_cause
            if brief.cause_confidence is not None:
                cause += f" (confidence: {brief.cause_confidence:.2f})"
            self._set("cause", f"Likely cause: {cause}")
        elif placeholder:
            self._set("cause", f"Likely cause: {placeholder}")
        else:
            self._set("cause", "")

        if brief.blast_radius:
            self._set("blast", f"Blast radius: {', '.join(brief.blast_radius)}")
        else:
            self._set("blast", "")

        if brief.state == "remediation_proposed":
            if brief.recommended_action:
                if brief.recommended_target:
                    self._set(
                        "recommended",
                        f"Recommended: {brief.recommended_action} on {brief.recommended_target}",
                    )
                else:
                    self._set("recommended", f"Recommended: {brief.recommended_action}")
            else:
                self._set("recommended", "Recommended: manual intervention required")
        elif brief.state == "investigation_complete":
            self._set("recommended", "Recommended: awaiting remediation proposal")
        else:
            self._set("recommended", "")

    def _render_error(self, message: str) -> None:
        # Inline error state — clears all positive-state fields so stale data
        # from a prior successful refresh doesn't mislead the operator.
        for field_id in ("header", "status", "summary", "cause", "blast", "recommended"):
            self._set(field_id, "")
        self._set("error", message)

    def _set(self, widget_id: str, text: str) -> None:
        widget = self.query_one(f"#{widget_id}", Static)
        widget.update(text)
