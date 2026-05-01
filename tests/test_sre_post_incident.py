"""Tests for ``nthlayer_bench.sre.post_incident``.

Logic-module tests use ``AsyncMock`` for ``CoreAPIClient`` and inject
``APIResult`` payloads. The post-incident review operates on JSON dicts
(HTTP response shape) — bench is HTTP-only.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from nthlayer_common.api_client import APIResult, CoreAPIClient

from nthlayer_bench.sre.post_incident import (
    AnchorVerdictMissingError,
    CaseNotFoundError,
    CoreUnreachableError,
    PostIncidentReview,
    build_post_incident_review,
    render_post_incident_review,
)


# ------------------------------------------------------------------ #
# Fixture builders                                                     #
# ------------------------------------------------------------------ #

def _verdict(
    *,
    verdict_id: str,
    subject_type: str,
    created_at: str,
    reasoning: str = "",
    confidence: float | None = 0.85,
    custom: dict | None = None,
    summary: str = "",
    producer: str = "nthlayer-respond",
    parent_ids: list[str] | None = None,
    outcome_status: str | None = None,
) -> dict:
    v = {
        "id": verdict_id,
        "created_at": created_at,
        "subject": {"type": subject_type, "ref": "INC-TEST", "summary": summary},
        "judgment": {
            "action": "flag",
            "confidence": confidence,
            "reasoning": reasoning,
        },
        "producer": {"system": producer, "instance": "test"},
        "metadata": {"custom": custom or {}},
    }
    if parent_ids is not None:
        v["parent_ids"] = parent_ids
    if outcome_status is not None:
        v["outcome_status"] = outcome_status
    return v


def _anchor() -> dict:
    return _verdict(
        verdict_id="vrd-anchor-001",
        subject_type="quality_breach",
        created_at="2026-04-28T09:55:00Z",
        reasoning="Reversal rate breach",
        producer="nthlayer-measure",
    )


def _case(
    case_id: str = "case-123",
    service: str = "fraud-detect",
    state: str = "resolved",
    created_at: str = "2026-04-28T09:55:00Z",
) -> dict:
    return {
        "id": case_id,
        "service": service,
        "underlying_verdict": "vrd-anchor-001",
        "state": state,
        "priority": "P1",
        "created_at": created_at,
    }


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


# ------------------------------------------------------------------ #
# Lifecycle state                                                      #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_resolved_case_yields_state_resolved():
    client = _make_client(
        case=_case(state="resolved"),
        anchor=_anchor(),
        descendants=[],
    )
    review = await build_post_incident_review(client, "case-123")
    assert review.state == "resolved"


@pytest.mark.asyncio
async def test_open_case_yields_state_in_progress():
    client = _make_client(
        case=_case(state="pending"),
        anchor=_anchor(),
        descendants=[],
    )
    review = await build_post_incident_review(client, "case-123")
    assert review.state == "in_progress"


# ------------------------------------------------------------------ #
# Timeline                                                             #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_timeline_is_chronological_ascending():
    """Timeline order is opposite of brief's latest-first — chronological
    by created_at, ties broken by verdict_id."""
    triage = _verdict(
        verdict_id="vrd-triage-001",
        subject_type="triage",
        created_at="2026-04-28T10:00:00Z",
        reasoning="SEV-2 triage",
    )
    correlation = _verdict(
        verdict_id="vrd-correlation-001",
        subject_type="correlation",
        created_at="2026-04-28T10:01:00Z",
        reasoning="bad deploy",
    )
    remediation = _verdict(
        verdict_id="vrd-remediation-001",
        subject_type="remediation",
        created_at="2026-04-28T10:02:00Z",
        custom={"proposed_action": "rollback", "target": "fraud-detect"},
    )
    client = _make_client(
        case=_case(),
        anchor=_anchor(),
        descendants=[remediation, triage, correlation],  # deliberately scrambled
    )

    review = await build_post_incident_review(client, "case-123")

    roles_in_order = [entry.role for entry in review.timeline]
    assert roles_in_order == ["quality_breach", "triage", "correlation", "remediation"]


@pytest.mark.asyncio
async def test_timeline_entry_summary_falls_back_to_reasoning():
    """When subject.summary is empty but judgment.reasoning has content,
    the timeline entry surfaces the reasoning so operators see something."""
    triage = _verdict(
        verdict_id="vrd-triage-001",
        subject_type="triage",
        created_at="2026-04-28T10:00:00Z",
        summary="",
        reasoning="reversal rate at 8%, target 1.5%",
    )
    client = _make_client(case=_case(), anchor=_anchor(), descendants=[triage])

    review = await build_post_incident_review(client, "case-123")

    triage_entry = next(e for e in review.timeline if e.role == "triage")
    assert triage_entry.summary == "reversal rate at 8%, target 1.5%"


# ------------------------------------------------------------------ #
# Worked / to-improve classification                                   #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_confirmed_outcome_classified_as_worked():
    triage = _verdict(
        verdict_id="vrd-triage-001",
        subject_type="triage",
        created_at="2026-04-28T10:00:00Z",
        summary="SEV-2: fraud-detect reversal rate breach",
    )
    outcome = _verdict(
        verdict_id="out-vrd-triage-001",
        subject_type="outcome_resolution",
        created_at="2026-04-28T11:00:00Z",
        parent_ids=["vrd-triage-001"],
        outcome_status="confirmed",
    )
    client = _make_client(case=_case(), anchor=_anchor(), descendants=[triage, outcome])

    review = await build_post_incident_review(client, "case-123")

    assert any("triage" in line and "fraud-detect" in line for line in review.worked)
    assert review.to_improve == []


@pytest.mark.asyncio
async def test_overridden_outcome_classified_as_to_improve():
    remediation = _verdict(
        verdict_id="vrd-remediation-001",
        subject_type="remediation",
        created_at="2026-04-28T10:02:00Z",
        summary="approved: rollback on fraud-detect",
        custom={"proposed_action": "rollback", "target": "fraud-detect"},
    )
    outcome = _verdict(
        verdict_id="out-vrd-remediation-001",
        subject_type="outcome_resolution",
        created_at="2026-04-28T11:00:00Z",
        parent_ids=["vrd-remediation-001"],
        outcome_status="overridden",
    )
    client = _make_client(
        case=_case(),
        anchor=_anchor(),
        descendants=[remediation, outcome],
    )

    review = await build_post_incident_review(client, "case-123")

    assert any("remediation" in line for line in review.to_improve)
    assert review.worked == []


@pytest.mark.asyncio
async def test_other_outcome_statuses_not_classified():
    """Statuses other than confirmed/overridden (partial, superseded,
    expired, None) are intentionally left out of the bullets — surface
    only clear signals."""
    triage = _verdict(
        verdict_id="vrd-triage-001",
        subject_type="triage",
        created_at="2026-04-28T10:00:00Z",
        summary="triage",
    )
    outcome = _verdict(
        verdict_id="out-vrd-triage-001",
        subject_type="outcome_resolution",
        created_at="2026-04-28T11:00:00Z",
        parent_ids=["vrd-triage-001"],
        outcome_status="partial",
    )
    client = _make_client(case=_case(), anchor=_anchor(), descendants=[triage, outcome])

    review = await build_post_incident_review(client, "case-123")

    assert review.worked == []
    assert review.to_improve == []


@pytest.mark.asyncio
async def test_latest_outcome_resolution_wins():
    """If a verdict has been resolved twice (resupersession), the latest
    outcome by created_at is the authoritative one."""
    triage = _verdict(
        verdict_id="vrd-triage-001",
        subject_type="triage",
        created_at="2026-04-28T10:00:00Z",
        summary="triage",
    )
    earlier_outcome = _verdict(
        verdict_id="out-vrd-triage-001-a",
        subject_type="outcome_resolution",
        created_at="2026-04-28T11:00:00Z",
        parent_ids=["vrd-triage-001"],
        outcome_status="overridden",
    )
    later_outcome = _verdict(
        verdict_id="out-vrd-triage-001-b",
        subject_type="outcome_resolution",
        created_at="2026-04-28T12:00:00Z",
        parent_ids=["vrd-triage-001"],
        outcome_status="confirmed",
    )
    client = _make_client(
        case=_case(),
        anchor=_anchor(),
        descendants=[triage, earlier_outcome, later_outcome],
    )

    review = await build_post_incident_review(client, "case-123")

    # Latest outcome (confirmed) wins → in worked, not to_improve.
    assert any("triage" in line for line in review.worked)
    assert review.to_improve == []


# ------------------------------------------------------------------ #
# Per-verdict accuracy                                                 #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_accuracy_pairs_verdict_with_outcome():
    triage = _verdict(
        verdict_id="vrd-triage-001",
        subject_type="triage",
        created_at="2026-04-28T10:00:00Z",
        confidence=0.85,
    )
    outcome = _verdict(
        verdict_id="out-vrd-triage-001",
        subject_type="outcome_resolution",
        created_at="2026-04-28T11:00:00Z",
        parent_ids=["vrd-triage-001"],
        outcome_status="confirmed",
    )
    client = _make_client(case=_case(), anchor=_anchor(), descendants=[triage, outcome])

    review = await build_post_incident_review(client, "case-123")

    triage_record = next(r for r in review.accuracy if r.verdict_id == "vrd-triage-001")
    assert triage_record.confidence == 0.85
    assert triage_record.outcome_status == "confirmed"
    assert triage_record.role == "triage"


@pytest.mark.asyncio
async def test_accuracy_unresolved_verdict_outcome_is_none():
    triage = _verdict(
        verdict_id="vrd-triage-001",
        subject_type="triage",
        created_at="2026-04-28T10:00:00Z",
        confidence=0.8,
    )
    client = _make_client(case=_case(), anchor=_anchor(), descendants=[triage])

    review = await build_post_incident_review(client, "case-123")

    triage_record = next(r for r in review.accuracy if r.verdict_id == "vrd-triage-001")
    assert triage_record.outcome_status is None


@pytest.mark.asyncio
async def test_accuracy_excludes_outcome_resolution_verdicts():
    """Outcome resolution verdicts themselves don't get an accuracy row
    — they're meta-verdicts that resolve other verdicts, not subjects of
    accuracy in their own right."""
    triage = _verdict(
        verdict_id="vrd-triage-001",
        subject_type="triage",
        created_at="2026-04-28T10:00:00Z",
    )
    outcome = _verdict(
        verdict_id="out-vrd-triage-001",
        subject_type="outcome_resolution",
        created_at="2026-04-28T11:00:00Z",
        parent_ids=["vrd-triage-001"],
        outcome_status="confirmed",
    )
    client = _make_client(case=_case(), anchor=_anchor(), descendants=[triage, outcome])

    review = await build_post_incident_review(client, "case-123")

    accuracy_ids = {r.verdict_id for r in review.accuracy}
    assert "out-vrd-triage-001" not in accuracy_ids


# ------------------------------------------------------------------ #
# Severity, duration, edge cases                                       #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_severity_pulled_from_latest_triage():
    triage_v1 = _verdict(
        verdict_id="vrd-triage-001",
        subject_type="triage",
        created_at="2026-04-28T10:00:00Z",
        custom={"severity": 3},
    )
    triage_v2 = _verdict(
        verdict_id="vrd-triage-002",
        subject_type="triage",
        created_at="2026-04-28T10:30:00Z",
        custom={"severity": 1},
    )
    client = _make_client(
        case=_case(),
        anchor=_anchor(),
        descendants=[triage_v1, triage_v2],
    )

    review = await build_post_incident_review(client, "case-123")

    assert review.severity == 1


@pytest.mark.asyncio
async def test_severity_none_when_no_triage():
    client = _make_client(case=_case(), anchor=_anchor(), descendants=[])
    review = await build_post_incident_review(client, "case-123")
    assert review.severity is None


@pytest.mark.asyncio
async def test_duration_minutes_computed_from_case_to_last_event():
    triage = _verdict(
        verdict_id="vrd-triage-001",
        subject_type="triage",
        created_at="2026-04-28T10:35:00Z",  # 40 min after case
    )
    client = _make_client(
        case=_case(created_at="2026-04-28T09:55:00Z"),
        anchor=_anchor(),
        descendants=[triage],
    )

    review = await build_post_incident_review(client, "case-123")

    assert review.duration_minutes == 40


@pytest.mark.asyncio
async def test_duration_minutes_none_when_chain_empty():
    """An anchor exists but no descendants — chain is non-empty (anchor
    only), so duration is computable from anchor's created_at. This pins
    the behavior for the 'minimal' case."""
    client = _make_client(case=_case(), anchor=_anchor(), descendants=[])
    review = await build_post_incident_review(client, "case-123")
    # Chain has anchor (created at 09:55) and case is at 09:55 → 0 min.
    assert review.duration_minutes == 0


# ------------------------------------------------------------------ #
# Error handling                                                       #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_case_not_found_raises_case_not_found_error():
    client = _make_client(case=None, case_status=404)
    with pytest.raises(CaseNotFoundError):
        await build_post_incident_review(client, "case-missing")


@pytest.mark.asyncio
async def test_anchor_missing_raises_anchor_verdict_missing_error():
    client = _make_client(case=_case(), anchor=None, anchor_status=404)
    with pytest.raises(AnchorVerdictMissingError):
        await build_post_incident_review(client, "case-123")


@pytest.mark.asyncio
async def test_connection_failed_raises_core_unreachable():
    client = _make_client(
        case=_case(),
        anchor=_anchor(),
        descendants=None,
        descendants_status=0,
    )
    with pytest.raises(CoreUnreachableError):
        await build_post_incident_review(client, "case-123")


# ------------------------------------------------------------------ #
# R5 Pass 3: defensive edge cases                                      #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_duration_minutes_none_on_mixed_naive_and_aware_datetimes():
    """Migration-era payloads can mix tz-naive and tz-aware ISO strings;
    subtracting them raises TypeError. Fail closed to None rather than
    crashing the refresh."""
    triage = _verdict(
        verdict_id="vrd-triage-001",
        subject_type="triage",
        created_at="2026-04-28T10:35:00+00:00",  # tz-aware
    )
    client = _make_client(
        case=_case(created_at="2026-04-28T09:55:00"),  # tz-naive
        anchor=_anchor(),
        descendants=[triage],
    )

    review = await build_post_incident_review(client, "case-123")

    assert review.duration_minutes is None  # fail-closed, not crash


@pytest.mark.asyncio
async def test_outcome_with_missing_created_at_is_skipped():
    """An outcome_resolution missing `created_at` can't be reliably
    ordered via the latest-wins tiebreaker — it must be skipped so its
    empty timestamp doesn't shadow a well-formed predecessor."""
    triage = _verdict(
        verdict_id="vrd-triage-001",
        subject_type="triage",
        created_at="2026-04-28T10:00:00Z",
        summary="triage",
    )
    valid_outcome = _verdict(
        verdict_id="out-vrd-triage-001-good",
        subject_type="outcome_resolution",
        created_at="2026-04-28T11:00:00Z",
        parent_ids=["vrd-triage-001"],
        outcome_status="confirmed",
    )
    bad_outcome = _verdict(
        verdict_id="out-vrd-triage-001-bad",
        subject_type="outcome_resolution",
        created_at="",  # malformed
        parent_ids=["vrd-triage-001"],
        outcome_status="overridden",
    )
    client = _make_client(
        case=_case(),
        anchor=_anchor(),
        descendants=[triage, valid_outcome, bad_outcome],
    )

    review = await build_post_incident_review(client, "case-123")

    # The valid outcome (confirmed) wins; the malformed one is ignored.
    assert any("triage" in line for line in review.worked)
    assert review.to_improve == []


@pytest.mark.asyncio
async def test_outcome_with_empty_parent_ids_does_not_crash():
    """An outcome_resolution carrying an empty parent_ids list shouldn't
    crash and shouldn't produce spurious accuracy entries."""
    triage = _verdict(
        verdict_id="vrd-triage-001",
        subject_type="triage",
        created_at="2026-04-28T10:00:00Z",
    )
    orphan_outcome = _verdict(
        verdict_id="out-orphan",
        subject_type="outcome_resolution",
        created_at="2026-04-28T11:00:00Z",
        parent_ids=[],  # explicitly empty
        outcome_status="confirmed",
    )
    client = _make_client(
        case=_case(),
        anchor=_anchor(),
        descendants=[triage, orphan_outcome],
    )

    review = await build_post_incident_review(client, "case-123")

    # No outcome was attributed to any verdict.
    triage_record = next(r for r in review.accuracy if r.verdict_id == "vrd-triage-001")
    assert triage_record.outcome_status is None


@pytest.mark.asyncio
async def test_verdict_missing_id_is_skipped_from_accuracy():
    """A descendant verdict with no `id` can't be referenced by an
    outcome's parent_ids, so accuracy can't say anything about it.
    Skip rather than emit a record with verdict_id="" which would later
    confuse renderers and exports."""
    triage = _verdict(
        verdict_id="vrd-triage-001",
        subject_type="triage",
        created_at="2026-04-28T10:00:00Z",
    )
    malformed = {
        "created_at": "2026-04-28T10:01:00Z",
        "subject": {"type": "investigation"},
        "judgment": {"action": "flag", "confidence": 0.5, "reasoning": "no id"},
        # no "id" key
    }
    client = _make_client(
        case=_case(),
        anchor=_anchor(),
        descendants=[triage, malformed],
    )

    review = await build_post_incident_review(client, "case-123")

    accuracy_ids = {r.verdict_id for r in review.accuracy}
    assert "" not in accuracy_ids


@pytest.mark.asyncio
async def test_severity_non_int_falls_back_to_none():
    """A string-typed severity from a deserialisation slip would otherwise
    reach the renderer's `f"P{n}"` format and produce garbled output
    ("PSEV-2"). Mirror brief.py's policy: never trust a non-int."""
    triage = _verdict(
        verdict_id="vrd-triage-001",
        subject_type="triage",
        created_at="2026-04-28T10:00:00Z",
        custom={"severity": "SEV-2"},  # string, not int
    )
    client = _make_client(case=_case(), anchor=_anchor(), descendants=[triage])

    review = await build_post_incident_review(client, "case-123")

    assert review.severity is None


@pytest.mark.asyncio
async def test_outcome_resolution_via_verdict_type_field_is_detected():
    """Some core deployments carry the type on `verdict_type` rather
    than `subject.type`. Pin the dual-path detection so the matching
    logic doesn't silently miss outcomes when the producer evolves."""
    triage = _verdict(
        verdict_id="vrd-triage-001",
        subject_type="triage",
        created_at="2026-04-28T10:00:00Z",
        summary="triage",
    )
    # subject.type is something else, but verdict_type marks it as the outcome.
    outcome = {
        "id": "out-via-verdict-type",
        "created_at": "2026-04-28T11:00:00Z",
        "subject": {"type": "operator_note"},
        "judgment": {"action": "approve", "confidence": 1.0, "reasoning": "r"},
        "metadata": {"custom": {}},
        "parent_ids": ["vrd-triage-001"],
        "verdict_type": "outcome_resolution",
        "outcome_status": "confirmed",
    }
    client = _make_client(case=_case(), anchor=_anchor(), descendants=[triage, outcome])

    review = await build_post_incident_review(client, "case-123")

    assert any("triage" in line for line in review.worked)
    triage_record = next(r for r in review.accuracy if r.verdict_id == "vrd-triage-001")
    assert triage_record.outcome_status == "confirmed"


# ------------------------------------------------------------------ #
# Renderer                                                             #
# ------------------------------------------------------------------ #

class TestRenderPostIncidentReview:
    def _make_review(self, **kwargs) -> PostIncidentReview:
        defaults = dict(
            case_id="case-123",
            service="fraud-detect",
            severity=2,
            state="resolved",
            duration_minutes=40,
        )
        defaults.update(kwargs)
        return PostIncidentReview(**defaults)

    def test_header_contains_case_service_severity(self):
        text = render_post_incident_review(self._make_review())
        assert "Post-incident review: case-123" in text
        assert "Service: fraud-detect" in text
        assert "Severity: P2" in text
        assert "State: resolved" in text
        assert "Duration: 40 minutes" in text

    def test_severity_unknown_renders_p_question(self):
        text = render_post_incident_review(self._make_review(severity=None))
        assert "Severity: P?" in text

    def test_in_progress_state_emits_draft_banner(self):
        text = render_post_incident_review(self._make_review(state="in_progress"))
        assert "DRAFT" in text
        assert "still in progress" in text

    def test_resolved_state_no_draft_banner(self):
        text = render_post_incident_review(self._make_review(state="resolved"))
        assert "DRAFT" not in text

    def test_empty_timeline_renders_placeholder(self):
        text = render_post_incident_review(self._make_review(timeline=[]))
        assert "(no events yet)" in text

    def test_section_headers_present(self):
        text = render_post_incident_review(self._make_review())
        assert "## Timeline" in text
        assert "## What worked" in text
        assert "## What to improve" in text
        assert "## Verdict accuracy" in text

    def test_empty_worked_renders_placeholder(self):
        text = render_post_incident_review(self._make_review())
        assert "(no confirmed outcomes yet)" in text

    def test_empty_to_improve_renders_placeholder(self):
        text = render_post_incident_review(self._make_review())
        assert "(no overridden outcomes yet)" in text
