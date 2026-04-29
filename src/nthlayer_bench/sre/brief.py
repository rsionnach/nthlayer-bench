"""Paging brief — current-state snapshot for the case-detail right pane.

Walks descendants of the case's ``underlying_verdict`` (Bead 1's
trigger anchor), picks the latest verdict for each respond role
(triage, correlation, remediation), and assembles a structured
``PagingBrief`` answering: what's broken, why, what can I do?

Every field traces to a verdict — no LLM call, no hallucination.

Spec: ``docs/superpowers/specs/2026-04-28-p4-bench-brief-design.md``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from nthlayer_common.api_client import CoreAPIClient

BriefState = Literal[
    "minimal",                  # Anchor verdict only; no respond chain yet
    "triage_complete",          # Triage verdict present
    "investigation_complete",   # Triage + correlation present
    "remediation_proposed",     # Triage + correlation + remediation present
]


@dataclass
class PagingBrief:
    """Structured paging brief assembled from the case's verdict chain."""

    case_id: str
    service: str
    severity: int | None
    summary: str
    likely_cause: str | None = None
    cause_confidence: float | None = None
    blast_radius: list[str] = field(default_factory=list)
    recommended_action: str | None = None
    recommended_target: str | None = None
    state: BriefState = "minimal"
    awaiting: list[str] = field(default_factory=list)


class BriefError(Exception):
    """Raised when the brief cannot be built."""


class CaseNotFoundError(BriefError):
    """The case_id does not exist in core."""


class AnchorVerdictMissingError(BriefError):
    """The case's underlying_verdict was not found — data integrity issue."""


class CoreUnreachableError(BriefError):
    """Core's HTTP API is unreachable (connection failed or non-2xx)."""


# State derivation table. Keys are (triage_present, correlation_present,
# remediation_present); values are (state, awaiting_list).
#
# Inconsistency case — correlation present without triage — is treated as
# "minimal" (the anchor is the summary source). This shouldn't occur in
# v1.5's respond pipeline (triage emits first); flagging the anomaly belongs
# in respond's instrumentation, not here.
_STATE_TABLE: dict[tuple[bool, bool, bool], tuple[BriefState, list[str]]] = {
    (False, False, False): ("minimal",                ["triage", "correlation", "remediation"]),
    (False, False, True):  ("minimal",                ["triage", "correlation", "remediation"]),
    (False, True,  False): ("minimal",                ["triage", "correlation", "remediation"]),
    (False, True,  True):  ("minimal",                ["triage", "correlation", "remediation"]),
    (True,  False, False): ("triage_complete",        ["correlation", "remediation"]),
    (True,  False, True):  ("triage_complete",        ["correlation", "remediation"]),
    (True,  True,  False): ("investigation_complete", ["remediation"]),
    (True,  True,  True):  ("remediation_proposed",   []),
}


def _derive_state(
    triage: dict | None,
    correlation: dict | None,
    remediation: dict | None,
) -> tuple[BriefState, list[str]]:
    key = (triage is not None, correlation is not None, remediation is not None)
    state, awaiting = _STATE_TABLE[key]
    # Return a fresh list so callers can mutate without affecting the table.
    return state, list(awaiting)


def _sort_key(verdict: dict) -> tuple[str, str]:
    # Tuple of (created_at, id). Caller sorts with reverse=True so most-recent
    # comes first; on tie, the higher verdict_id wins. Tie-breaker is
    # deterministic but arbitrary; at v1.5 demo scale simultaneous verdicts
    # are unlikely. If production usage surfaces ordering issues, switch to
    # a chain-depth tiebreaker via parent_ids.
    return (verdict.get("created_at", ""), verdict.get("id", ""))


async def build_paging_brief(
    client: CoreAPIClient,
    case_id: str,
) -> PagingBrief:
    """Build a paging brief from the current state of a case's verdict chain.

    Walks descendants of ``case["underlying_verdict"]``, selects the latest
    verdict per respond role, and projects them into a ``PagingBrief``.
    """
    case = await _get_case(client, case_id)
    anchor_id = case["underlying_verdict"]
    service = case.get("service") or "unknown"

    anchor = await _get_anchor(client, anchor_id)
    descendants = await _get_descendants(client, anchor_id)

    chain = sorted([anchor, *descendants], key=_sort_key, reverse=True)

    latest_by_role: dict[str, dict] = {}
    for v in chain:
        role = (v.get("subject") or {}).get("type")
        if role:
            latest_by_role.setdefault(role, v)

    triage = latest_by_role.get("triage")
    correlation = latest_by_role.get("correlation")
    remediation = latest_by_role.get("remediation")

    severity, blast_radius, summary = _project_triage(triage, anchor)
    likely_cause, cause_confidence = _project_correlation(correlation)
    recommended_action, recommended_target = _project_remediation(remediation)
    state, awaiting = _derive_state(triage, correlation, remediation)

    return PagingBrief(
        case_id=case_id,
        service=service,
        severity=severity,
        summary=summary,
        likely_cause=likely_cause,
        cause_confidence=cause_confidence,
        blast_radius=blast_radius,
        recommended_action=recommended_action,
        recommended_target=recommended_target,
        state=state,
        awaiting=awaiting,
    )


async def _get_case(client: CoreAPIClient, case_id: str) -> dict:
    result = await client.get_case(case_id)
    if result.ok:
        return result.data
    if result.status_code == 404:
        raise CaseNotFoundError(case_id)
    if result.status_code == 0:
        raise CoreUnreachableError(result.detail or {"error": result.error})
    raise BriefError(f"get_case failed: {result.error} (status={result.status_code})")


async def _get_anchor(client: CoreAPIClient, anchor_id: str) -> dict:
    result = await client.get_verdict(anchor_id)
    if result.ok:
        return result.data
    if result.status_code == 404:
        raise AnchorVerdictMissingError(anchor_id)
    if result.status_code == 0:
        raise CoreUnreachableError(result.detail or {"error": result.error})
    raise BriefError(f"get_verdict failed: {result.error} (status={result.status_code})")


async def _get_descendants(client: CoreAPIClient, anchor_id: str) -> list[dict]:
    result = await client.get_descendants(anchor_id)
    if result.ok:
        return result.data or []
    if result.status_code == 0:
        raise CoreUnreachableError(result.detail or {"error": result.error})
    raise BriefError(
        f"get_descendants failed: {result.error} (status={result.status_code})"
    )


def _project_triage(
    triage: dict | None,
    anchor: dict,
) -> tuple[int | None, list[str], str]:
    if triage is None:
        # Minimal-state fallback: anchor's reasoning carries what's known.
        anchor_reasoning = (anchor.get("judgment") or {}).get("reasoning", "")
        return None, [], anchor_reasoning

    custom = (triage.get("metadata") or {}).get("custom") or {}
    severity = custom.get("severity")  # None when missing — no fabricated P3.
    blast_radius = list(custom.get("blast_radius") or [])
    summary = (triage.get("judgment") or {}).get("reasoning", "")
    return severity, blast_radius, summary


def _project_correlation(
    correlation: dict | None,
) -> tuple[str | None, float | None]:
    if correlation is None:
        return None, None
    judgment = correlation.get("judgment") or {}
    return judgment.get("reasoning"), judgment.get("confidence")


def _project_remediation(
    remediation: dict | None,
) -> tuple[str | None, str | None]:
    if remediation is None:
        return None, None
    custom = (remediation.get("metadata") or {}).get("custom") or {}
    return custom.get("proposed_action"), custom.get("target")


# Severity emoji and label tables — preserve legacy renderer parity.
_SEVERITY_EMOJI = {1: "\U0001f534", 2: "\U0001f7e0", 3: "\U0001f7e1", 4: "\U0001f535"}
_SEVERITY_LABEL = {1: "P1", 2: "P2", 3: "P3", 4: "P4"}


def render_brief(brief: PagingBrief) -> str:
    """Render a ``PagingBrief`` to plain text.

    Used by tests and any future text-only consumer (CLI wrapper, log
    emission). The Textual widget renders independently using primitives.
    """
    if brief.severity is None:
        header = f"Severity: unknown — {brief.service}"
    else:
        emoji = _SEVERITY_EMOJI.get(brief.severity, "")
        label = _SEVERITY_LABEL.get(brief.severity, f"P{brief.severity}")
        prefix = f"{emoji} {label}".strip()
        header = f"{prefix}: {brief.service}"

    lines: list[str] = [header]

    if brief.state != "remediation_proposed":
        if brief.awaiting:
            lines.append(f"Status: {brief.state} (awaiting: {', '.join(brief.awaiting)})")
        else:
            lines.append(f"Status: {brief.state}")

    lines.append("")
    lines.append(f"What's happening: {brief.summary}")

    if brief.likely_cause:
        if brief.cause_confidence is not None:
            lines.append(
                f"Likely cause: {brief.likely_cause} (confidence: {brief.cause_confidence:.2f})"
            )
        else:
            lines.append(f"Likely cause: {brief.likely_cause}")
    else:
        lines.append("Likely cause: Investigation in progress")

    if brief.blast_radius:
        lines.append(f"Blast radius: {', '.join(brief.blast_radius)}")

    if brief.state == "remediation_proposed":
        if brief.recommended_action:
            if brief.recommended_target:
                lines.append(
                    f"Recommended: {brief.recommended_action} on {brief.recommended_target}"
                )
            else:
                lines.append(f"Recommended: {brief.recommended_action}")
        else:
            # Degraded remediation: a verdict was emitted but the agent had no
            # action to propose. Surface the human-takeover ask explicitly.
            lines.append("Recommended: manual intervention required")
    # Pre-`remediation_proposed` states never show a Recommended line — even if
    # an orphan remediation verdict made `recommended_action` non-None on a
    # system-inconsistency case, the chain is too partial to be reliable.

    lines.append("")
    lines.append(f"Case: {brief.case_id}")

    return "\n".join(lines)
