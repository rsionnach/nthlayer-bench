"""Tests for ``nthlayer_bench.sre.brief``.

Logic-module tests use ``AsyncMock`` for ``CoreAPIClient`` and inject
``APIResult`` payloads. The brief operates on JSON dicts (HTTP response
shape) — bench is HTTP-only — so fixtures are dicts not dataclasses.

Test cases mirror the 15 cases in the spec
(``docs/superpowers/specs/2026-04-28-p4-bench-brief-design.md`` §Testing).
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from nthlayer_common.api_client import APIResult, CoreAPIClient

from nthlayer_bench.sre.brief import (
    AnchorVerdictMissingError,
    BriefError,
    CaseNotFoundError,
    CoreUnreachableError,
    PagingBrief,
    build_paging_brief,
    render_brief,
)


# ------------------------------------------------------------------ #
# Fixture builders                                                     #
# ------------------------------------------------------------------ #

def _verdict(
    *,
    verdict_id: str,
    subject_type: str,
    created_at: str = "2026-04-28T10:00:00Z",
    reasoning: str = "",
    confidence: float | None = 0.85,
    custom: dict | None = None,
) -> dict:
    return {
        "id": verdict_id,
        "created_at": created_at,
        "subject": {"type": subject_type, "ref": "INC-TEST", "summary": ""},
        "judgment": {
            "action": "flag",
            "confidence": confidence,
            "reasoning": reasoning,
        },
        "metadata": {"custom": custom or {}},
    }


def _anchor(reasoning: str = "Reversal rate breach detected") -> dict:
    return _verdict(
        verdict_id="vrd-anchor-001",
        subject_type="quality_breach",
        created_at="2026-04-28T09:55:00Z",
        reasoning=reasoning,
    )


def _make_client(
    *,
    case: dict | None = None,
    case_status: int = 200,
    anchor: dict | None = None,
    anchor_status: int = 200,
    descendants: list[dict] | None = None,
    descendants_status: int = 200,
) -> AsyncMock:
    client = AsyncMock(spec=CoreAPIClient)
    client.get_case.return_value = APIResult(
        ok=(case_status == 200),
        status_code=case_status,
        data=case,
        error=None if case_status == 200 else f"http_{case_status}",
    )
    client.get_verdict.return_value = APIResult(
        ok=(anchor_status == 200),
        status_code=anchor_status,
        data=anchor,
        error=None if anchor_status == 200 else f"http_{anchor_status}",
    )
    client.get_descendants.return_value = APIResult(
        ok=(descendants_status == 200),
        status_code=descendants_status,
        data=descendants,
        error=None if descendants_status == 200 else f"http_{descendants_status}",
    )
    return client


def _case(
    case_id: str = "case-123",
    service: str = "fraud-detect",
    underlying_verdict: str = "vrd-anchor-001",
) -> dict:
    return {
        "id": case_id,
        "service": service,
        "underlying_verdict": underlying_verdict,
        "state": "pending",
        "priority": "P1",
    }


# ------------------------------------------------------------------ #
# Spec test case 1: Happy path with full chain                         #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_happy_path_full_chain():
    triage = _verdict(
        verdict_id="vrd-triage-001",
        subject_type="triage",
        created_at="2026-04-28T10:00:00Z",
        reasoning="SEV-2: fraud-detect reversal_rate at 8%, target 1.5%",
        custom={"severity": 2, "blast_radius": ["fraud-detect", "payment-api"]},
    )
    correlation = _verdict(
        verdict_id="vrd-correlation-001",
        subject_type="correlation",
        created_at="2026-04-28T10:01:00Z",
        reasoning="Bad deploy v2.3.1 to fraud-detect 14m ago",
        confidence=0.74,
    )
    remediation = _verdict(
        verdict_id="vrd-remediation-001",
        subject_type="remediation",
        created_at="2026-04-28T10:02:00Z",
        reasoning="rollback recommended",
        custom={"proposed_action": "rollback", "target": "fraud-detect"},
    )
    client = _make_client(
        case=_case(),
        anchor=_anchor(),
        descendants=[triage, correlation, remediation],
    )

    brief = await build_paging_brief(client, "case-123")

    assert brief.state == "remediation_proposed"
    assert brief.awaiting == []
    assert brief.severity == 2
    assert brief.service == "fraud-detect"
    assert brief.blast_radius == ["fraud-detect", "payment-api"]
    assert brief.likely_cause == "Bad deploy v2.3.1 to fraud-detect 14m ago"
    assert brief.cause_confidence == 0.74
    assert brief.recommended_action == "rollback"
    assert brief.recommended_target == "fraud-detect"


# ------------------------------------------------------------------ #
# Spec test case 2: Triage only                                        #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_triage_only():
    triage = _verdict(
        verdict_id="vrd-triage-001",
        subject_type="triage",
        reasoning="SEV-2 triage",
        custom={"severity": 2, "blast_radius": ["fraud-detect"]},
    )
    client = _make_client(case=_case(), anchor=_anchor(), descendants=[triage])

    brief = await build_paging_brief(client, "case-123")

    assert brief.state == "triage_complete"
    assert brief.awaiting == ["correlation", "remediation"]
    assert brief.severity == 2
    assert brief.likely_cause is None
    assert brief.cause_confidence is None
    assert brief.recommended_action is None
    assert brief.recommended_target is None


# ------------------------------------------------------------------ #
# Spec test case 3: Triage + correlation, no remediation               #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_triage_and_correlation_no_remediation():
    triage = _verdict(
        verdict_id="vrd-triage-001",
        subject_type="triage",
        custom={"severity": 1, "blast_radius": []},
    )
    correlation = _verdict(
        verdict_id="vrd-correlation-001",
        subject_type="correlation",
        reasoning="Schema drift detected",
        confidence=0.62,
    )
    client = _make_client(
        case=_case(),
        anchor=_anchor(),
        descendants=[triage, correlation],
    )

    brief = await build_paging_brief(client, "case-123")

    assert brief.state == "investigation_complete"
    assert brief.awaiting == ["remediation"]
    assert brief.likely_cause == "Schema drift detected"
    assert brief.cause_confidence == 0.62
    assert brief.recommended_action is None


# ------------------------------------------------------------------ #
# Spec test case 4: Anchor only                                        #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_anchor_only_minimal_state():
    client = _make_client(
        case=_case(),
        anchor=_anchor("Reversal rate breach detected on fraud-detect"),
        descendants=[],
    )

    brief = await build_paging_brief(client, "case-123")

    assert brief.state == "minimal"
    assert brief.awaiting == ["triage", "correlation", "remediation"]
    assert brief.severity is None
    assert brief.summary == "Reversal rate breach detected on fraud-detect"
    assert brief.blast_radius == []


# ------------------------------------------------------------------ #
# Spec test case 5: Latest-of-role selection                           #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_latest_of_role_selected_by_created_at():
    older = _verdict(
        verdict_id="vrd-triage-001",
        subject_type="triage",
        created_at="2026-04-28T10:00:00Z",
        reasoning="initial triage",
        custom={"severity": 3},
    )
    newer = _verdict(
        verdict_id="vrd-triage-002",
        subject_type="triage",
        created_at="2026-04-28T10:05:00Z",
        reasoning="re-triage after escalation",
        custom={"severity": 1},
    )
    client = _make_client(
        case=_case(),
        anchor=_anchor(),
        descendants=[older, newer],
    )

    brief = await build_paging_brief(client, "case-123")

    assert brief.severity == 1, "Newer triage's severity should win"
    assert brief.summary == "re-triage after escalation"


# ------------------------------------------------------------------ #
# Spec test case 6: Tie-breaker on identical created_at                #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_tiebreaker_higher_verdict_id_wins():
    same_time = "2026-04-28T10:00:00Z"
    a = _verdict(
        verdict_id="vrd-triage-001",
        subject_type="triage",
        created_at=same_time,
        reasoning="A",
        custom={"severity": 2},
    )
    b = _verdict(
        verdict_id="vrd-triage-002",
        subject_type="triage",
        created_at=same_time,
        reasoning="B",
        custom={"severity": 1},
    )
    client = _make_client(case=_case(), anchor=_anchor(), descendants=[a, b])

    brief = await build_paging_brief(client, "case-123")

    # Higher verdict_id (002 > 001) wins on tie. Documents the chosen rule.
    assert brief.summary == "B"
    assert brief.severity == 1


# ------------------------------------------------------------------ #
# Spec test case 7: Severity missing from custom                       #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_severity_missing_returns_none_no_fabricated_p3():
    triage = _verdict(
        verdict_id="vrd-triage-001",
        subject_type="triage",
        reasoning="triage without severity",
        custom={"blast_radius": ["fraud-detect"]},  # no severity key
    )
    client = _make_client(case=_case(), anchor=_anchor(), descendants=[triage])

    brief = await build_paging_brief(client, "case-123")

    assert brief.severity is None
    assert brief.state == "triage_complete"


# ------------------------------------------------------------------ #
# Spec test case 8: Blast radius missing                               #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_blast_radius_missing_returns_empty_list():
    triage = _verdict(
        verdict_id="vrd-triage-001",
        subject_type="triage",
        custom={"severity": 2},  # no blast_radius
    )
    client = _make_client(case=_case(), anchor=_anchor(), descendants=[triage])

    brief = await build_paging_brief(client, "case-123")

    assert brief.blast_radius == []


# ------------------------------------------------------------------ #
# Spec test case 9: 404 on case                                        #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_case_not_found_raises_case_not_found_error():
    client = _make_client(case=None, case_status=404)

    with pytest.raises(CaseNotFoundError):
        await build_paging_brief(client, "case-missing")


# ------------------------------------------------------------------ #
# Spec test case 10: 404 on anchor verdict                             #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_anchor_verdict_missing_raises_anchor_verdict_missing_error():
    client = _make_client(
        case=_case(),
        anchor=None,
        anchor_status=404,
    )

    with pytest.raises(AnchorVerdictMissingError):
        await build_paging_brief(client, "case-123")


# ------------------------------------------------------------------ #
# Spec test case 11: Connection failed on get_descendants              #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_descendants_connection_failed_raises_core_unreachable():
    client = _make_client(
        case=_case(),
        anchor=_anchor(),
        descendants=None,
        descendants_status=0,  # connection_failed sentinel from APIResult
    )

    with pytest.raises(CoreUnreachableError):
        await build_paging_brief(client, "case-123")


# ------------------------------------------------------------------ #
# Spec test case 12: Correlation without triage (system inconsistency) #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_correlation_without_triage_treated_as_minimal():
    correlation = _verdict(
        verdict_id="vrd-correlation-001",
        subject_type="correlation",
        reasoning="orphan correlation",
        confidence=0.5,
    )
    client = _make_client(
        case=_case(),
        anchor=_anchor("anchor reason"),
        descendants=[correlation],
    )

    brief = await build_paging_brief(client, "case-123")

    assert brief.state == "minimal"
    assert brief.awaiting == ["triage", "correlation", "remediation"]
    # Anchor's reasoning (not the orphan correlation's) is the summary source.
    assert brief.summary == "anchor reason"


# ------------------------------------------------------------------ #
# Spec test case 13: Anchor reasoning fallback                         #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_anchor_reasoning_used_as_summary_in_minimal_state():
    client = _make_client(
        case=_case(),
        anchor=_anchor("Anchor's specific reasoning text"),
        descendants=[],
    )

    brief = await build_paging_brief(client, "case-123")

    assert brief.state == "minimal"
    assert brief.summary == "Anchor's specific reasoning text"


# ------------------------------------------------------------------ #
# Spec test case 14: Remediation with action set, target unset         #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_remediation_with_action_no_target_renders_action_alone():
    triage = _verdict(
        verdict_id="vrd-triage-001",
        subject_type="triage",
        custom={"severity": 2},
    )
    correlation = _verdict(
        verdict_id="vrd-correlation-001",
        subject_type="correlation",
        reasoning="cause",
        confidence=0.5,
    )
    remediation = _verdict(
        verdict_id="vrd-remediation-001",
        subject_type="remediation",
        custom={"proposed_action": "rollback", "target": None},
    )
    client = _make_client(
        case=_case(),
        anchor=_anchor(),
        descendants=[triage, correlation, remediation],
    )

    brief = await build_paging_brief(client, "case-123")

    assert brief.recommended_action == "rollback"
    assert brief.recommended_target is None
    text = render_brief(brief)
    [recommended_line] = [
        line for line in text.splitlines() if line.startswith("Recommended: ")
    ]
    assert recommended_line == "Recommended: rollback"


# ------------------------------------------------------------------ #
# Spec test case 15: Degraded remediation                              #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_degraded_remediation_state_is_remediation_proposed():
    triage = _verdict(
        verdict_id="vrd-triage-001",
        subject_type="triage",
        custom={"severity": 2},
    )
    correlation = _verdict(
        verdict_id="vrd-correlation-001",
        subject_type="correlation",
        reasoning="cause",
        confidence=0.5,
    )
    degraded = _verdict(
        verdict_id="vrd-remediation-001",
        subject_type="remediation",
        custom={"proposed_action": None, "target": None},
    )
    client = _make_client(
        case=_case(),
        anchor=_anchor(),
        descendants=[triage, correlation, degraded],
    )

    brief = await build_paging_brief(client, "case-123")

    # A remediation verdict was emitted, even if degraded — state advances.
    assert brief.state == "remediation_proposed"
    assert brief.recommended_action is None
    assert "manual intervention required" in render_brief(brief)


# ------------------------------------------------------------------ #
# Defensive guard tests (R5 Pass 3 missing-coverage additions)          #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_case_with_missing_service_field_renders_unknown():
    """Defensive: case dict without a `service` key (or service=None) →
    PagingBrief.service is "unknown". Don't fabricate a name."""
    case = {
        "id": "case-no-service",
        "underlying_verdict": "vrd-anchor-001",
        "state": "pending",
        "priority": "P3",
        # no "service" key at all
    }
    client = _make_client(case=case, anchor=_anchor(), descendants=[])

    brief = await build_paging_brief(client, "case-no-service")

    assert brief.service == "unknown"


@pytest.mark.asyncio
async def test_descendant_verdict_missing_subject_is_dropped():
    """Defensive: a malformed descendant with no `subject` key is silently
    dropped from the latest_by_role lookup (no role key to set). Doesn't
    crash; doesn't pollute state derivation."""
    triage = _verdict(
        verdict_id="vrd-triage-001",
        subject_type="triage",
        custom={"severity": 2},
    )
    malformed = {
        "id": "vrd-malformed",
        "created_at": "2026-04-28T10:30:00Z",
        # no subject, judgment, or metadata
    }
    client = _make_client(
        case=_case(),
        anchor=_anchor(),
        descendants=[triage, malformed],
    )

    brief = await build_paging_brief(client, "case-123")

    assert brief.state == "triage_complete"
    assert brief.severity == 2  # malformed verdict didn't crash projection


@pytest.mark.asyncio
async def test_triage_with_missing_metadata_uses_safe_defaults():
    """Defensive: a triage verdict with no `metadata` or `judgment` key
    doesn't crash projection. Severity is None, blast_radius is [],
    summary is empty."""
    triage = {
        "id": "vrd-triage-001",
        "created_at": "2026-04-28T10:00:00Z",
        "subject": {"type": "triage", "ref": "INC-X", "summary": ""},
        # no judgment, no metadata
    }
    client = _make_client(case=_case(), anchor=_anchor(), descendants=[triage])

    brief = await build_paging_brief(client, "case-123")

    assert brief.state == "triage_complete"
    assert brief.severity is None
    assert brief.blast_radius == []
    assert brief.summary == ""


@pytest.mark.asyncio
async def test_string_severity_renders_label_without_emoji():
    """Defensive: if a payload comes back with severity as a string '1'
    rather than int 1 (deserialization slip), the renderer falls back to
    'P1' label without an emoji rather than crashing."""
    triage = _verdict(
        verdict_id="vrd-triage-001",
        subject_type="triage",
        reasoning="string-severity case",
        custom={"severity": "1"},
    )
    client = _make_client(case=_case(), anchor=_anchor(), descendants=[triage])

    brief = await build_paging_brief(client, "case-123")
    text = render_brief(brief)

    # Label fallback uses f"P{severity}" → "P1" even when dict lookup misses.
    header_line = text.splitlines()[0]
    assert "P1" in header_line
    assert "fraud-detect" in header_line


# ------------------------------------------------------------------ #
# Renderer tests                                                       #
# ------------------------------------------------------------------ #

class TestRenderBrief:
    def test_severity_emoji_and_label(self):
        brief = PagingBrief(
            case_id="case-123",
            service="fraud-detect",
            severity=2,
            summary="reversal rate breach",
            state="remediation_proposed",
        )
        text = render_brief(brief)
        assert "P2" in text
        assert "fraud-detect" in text
        # No status line when state is remediation_proposed.
        assert "Status:" not in text

    def test_unknown_severity_renders_explicit_unknown(self):
        brief = PagingBrief(
            case_id="case-123",
            service="fraud-detect",
            severity=None,
            summary="unknown severity case",
            state="minimal",
            awaiting=["triage", "correlation", "remediation"],
        )
        text = render_brief(brief)
        assert "Severity: unknown" in text
        # Don't fabricate a P3.
        assert "P3" not in text

    def test_state_line_for_non_remediation_proposed_states(self):
        brief = PagingBrief(
            case_id="case-123",
            service="fraud-detect",
            severity=1,
            summary="triage in",
            state="triage_complete",
            awaiting=["correlation", "remediation"],
        )
        text = render_brief(brief)
        assert "Status: triage_complete" in text
        assert "awaiting: correlation, remediation" in text

    def test_blast_radius_line_present_when_set(self):
        brief = PagingBrief(
            case_id="case-123",
            service="fraud-detect",
            severity=1,
            summary="s",
            blast_radius=["fraud-detect", "payment-api"],
            state="triage_complete",
            awaiting=["correlation", "remediation"],
        )
        text = render_brief(brief)
        assert "Blast radius: fraud-detect, payment-api" in text

    def test_likely_cause_with_confidence(self):
        brief = PagingBrief(
            case_id="case-123",
            service="fraud-detect",
            severity=1,
            summary="s",
            likely_cause="bad deploy",
            cause_confidence=0.74,
            state="investigation_complete",
            awaiting=["remediation"],
        )
        text = render_brief(brief)
        assert "Likely cause: bad deploy (confidence: 0.74)" in text

    def test_recommended_action_with_target(self):
        brief = PagingBrief(
            case_id="case-123",
            service="fraud-detect",
            severity=1,
            summary="s",
            recommended_action="rollback",
            recommended_target="fraud-detect",
            state="remediation_proposed",
        )
        text = render_brief(brief)
        assert "Recommended: rollback on fraud-detect" in text

    def test_recommended_action_without_target(self):
        brief = PagingBrief(
            case_id="case-123",
            service="fraud-detect",
            severity=1,
            summary="s",
            recommended_action="rollback",
            recommended_target=None,
            state="remediation_proposed",
        )
        text = render_brief(brief)
        assert "Recommended: rollback" in text
        assert " on " not in text.split("Recommended: rollback")[1].splitlines()[0]

    def test_degraded_remediation_renders_manual_intervention(self):
        brief = PagingBrief(
            case_id="case-123",
            service="fraud-detect",
            severity=1,
            summary="s",
            recommended_action=None,
            state="remediation_proposed",
        )
        text = render_brief(brief)
        assert "Recommended: manual intervention required" in text

    def test_no_likely_cause_renders_investigation_in_progress(self):
        brief = PagingBrief(
            case_id="case-123",
            service="fraud-detect",
            severity=1,
            summary="s",
            likely_cause=None,
            state="triage_complete",
            awaiting=["correlation", "remediation"],
        )
        text = render_brief(brief)
        assert "Likely cause: Investigation in progress" in text

    def test_case_id_line_always_present(self):
        brief = PagingBrief(
            case_id="case-XYZ-9",
            service="fraud-detect",
            severity=1,
            summary="s",
            state="minimal",
            awaiting=["triage", "correlation", "remediation"],
        )
        assert "Case: case-XYZ-9" in render_brief(brief)
