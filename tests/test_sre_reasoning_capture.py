"""Tests for ``nthlayer_bench.sre.reasoning_capture``."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from nthlayer_common.api_client import APIResult, CoreAPIClient

from nthlayer_bench.sre.reasoning_capture import (
    AnchorVerdictMissingError,
    CaseNotFoundError,
    CoreUnreachableError,
    OperatorNote,
    ReasoningCaptureError,
    fetch_operator_notes,
    submit_operator_note,
)


# ------------------------------------------------------------------ #
# Fixture builders                                                     #
# ------------------------------------------------------------------ #

def _case(case_id: str = "case-123", service: str = "fraud-detect") -> dict:
    return {
        "id": case_id,
        "service": service,
        "underlying_verdict": "vrd-anchor-001",
        "state": "pending",
        "priority": "P1",
    }


def _operator_note_verdict(
    *,
    verdict_id: str,
    text: str,
    author: str = "alice",
    created_at: str = "2026-04-30T10:00:00Z",
    subject_type: str = "custom",
    verdict_type: str | None = "operator_note",
) -> dict:
    """Build an operator_note verdict in the wire shape core returns.

    Production shape uses ``subject.type="custom"`` (since
    ``operator_note`` isn't in VALID_SUBJECT_TYPES) and tags the role
    on ``verdict_type``. The defaults match production. Override
    ``subject_type``/``verdict_type`` to exercise the dual-path
    detection (legacy ``subject.type="operator_note"`` producers, or
    ``verdict_type``-only payloads)."""
    payload = {
        "id": verdict_id,
        "service": "fraud-detect",
        "created_at": created_at,
        "subject": {"type": subject_type, "ref": "case-123", "summary": text[:80]},
        "judgment": {"action": "flag", "confidence": 1.0, "reasoning": text},
        "metadata": {"custom": {"author": author}},
    }
    if verdict_type is not None:
        payload["verdict_type"] = verdict_type
    return payload


def _other_verdict(
    *,
    verdict_id: str = "vrd-other",
    subject_type: str = "triage",
) -> dict:
    """A non-operator-note verdict; should never appear in the notes feed."""
    return {
        "id": verdict_id,
        "created_at": "2026-04-30T10:00:00Z",
        "subject": {"type": subject_type, "ref": "case-123", "summary": "x"},
        "judgment": {"action": "flag", "confidence": 0.5, "reasoning": "x"},
        "metadata": {"custom": {}},
    }


def _make_client(
    *,
    case: dict | None = None,
    case_status: int = 200,
    descendants: list[dict] | None = None,
    descendants_status: int = 200,
    submit_status: int = 201,
    submit_error: str | None = None,
) -> AsyncMock:
    client = AsyncMock(spec=CoreAPIClient)
    client.get_case.return_value = APIResult(
        ok=(case_status == 200),
        status_code=case_status,
        data=case,
        error=None if case_status == 200 else f"http_{case_status}",
    )
    client.get_descendants.return_value = APIResult(
        ok=(descendants_status == 200),
        status_code=descendants_status,
        data=descendants,
        error=None if descendants_status == 200 else f"http_{descendants_status}",
    )
    client.submit_verdict.return_value = APIResult(
        ok=(200 <= submit_status < 300),
        status_code=submit_status,
        data={"id": "vrd-submitted"} if 200 <= submit_status < 300 else None,
        error=submit_error,
    )
    return client


# ------------------------------------------------------------------ #
# fetch_operator_notes                                                 #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_fetch_returns_notes_in_chronological_order():
    """Notes are sorted ascending by created_at — oldest first, so a
    reader follows the operator's reasoning trail in order."""
    earlier = _operator_note_verdict(
        verdict_id="vrd-note-1",
        text="initial assessment",
        created_at="2026-04-30T10:00:00Z",
    )
    later = _operator_note_verdict(
        verdict_id="vrd-note-2",
        text="updated after lunch",
        created_at="2026-04-30T13:00:00Z",
    )
    client = _make_client(
        case=_case(),
        descendants=[later, earlier],  # arrive scrambled
    )

    notes = await fetch_operator_notes(client, "case-123")

    assert [n.verdict_id for n in notes] == ["vrd-note-1", "vrd-note-2"]


@pytest.mark.asyncio
async def test_fetch_filters_to_operator_notes_only():
    """Non-operator verdicts in the descendants chain (triage,
    correlation, remediation, etc.) are excluded — the notes panel
    shows only operator-authored content."""
    note = _operator_note_verdict(verdict_id="vrd-note-1", text="my note")
    triage = _other_verdict(verdict_id="vrd-triage", subject_type="triage")
    correlation = _other_verdict(
        verdict_id="vrd-correlation", subject_type="correlation"
    )
    client = _make_client(
        case=_case(),
        descendants=[note, triage, correlation],
    )

    notes = await fetch_operator_notes(client, "case-123")

    assert [n.verdict_id for n in notes] == ["vrd-note-1"]


@pytest.mark.asyncio
async def test_fetch_detects_legacy_operator_note_via_subject_type():
    """Legacy producers may have stored the role on
    ``subject.type="operator_note"`` directly (before the typed
    verdict_type column existed). Pin the dual-path detection so we
    don't silently hide migrated notes."""
    legacy = _operator_note_verdict(
        verdict_id="vrd-legacy",
        text="legacy shape",
        subject_type="operator_note",
        verdict_type=None,  # only subject.type carries the role
    )
    client = _make_client(case=_case(), descendants=[legacy])

    notes = await fetch_operator_notes(client, "case-123")

    assert len(notes) == 1
    assert notes[0].verdict_id == "vrd-legacy"


@pytest.mark.asyncio
async def test_fetch_detects_operator_note_via_verdict_type_only():
    """Verdicts that tag only ``verdict_type`` (subject.type set to
    something neutral like ``custom``) are still recognised — this is
    the production shape since operator_note isn't a valid subject.type."""
    typed = _operator_note_verdict(
        verdict_id="vrd-typed",
        text="typed-only",
        subject_type="custom",
        verdict_type="operator_note",
    )
    client = _make_client(case=_case(), descendants=[typed])

    notes = await fetch_operator_notes(client, "case-123")

    assert len(notes) == 1
    assert notes[0].verdict_id == "vrd-typed"


@pytest.mark.asyncio
async def test_fetch_extracts_author_text_and_timestamp():
    note = _operator_note_verdict(
        verdict_id="vrd-1",
        text="investigated, looks like the deploy",
        author="alice@nthlayer.com",
        created_at="2026-04-30T10:00:00Z",
    )
    client = _make_client(case=_case(), descendants=[note])

    notes = await fetch_operator_notes(client, "case-123")
    n = notes[0]

    assert n.author == "alice@nthlayer.com"
    assert n.text == "investigated, looks like the deploy"
    assert n.created_at == "2026-04-30T10:00:00Z"
    assert n.case_id == "case-123"


@pytest.mark.asyncio
async def test_fetch_falls_back_to_unknown_author_when_missing():
    """Defensive: a payload without ``metadata.custom.author`` (older
    producer, partial migration) yields ``"unknown"`` rather than an
    empty string or None — operators see explicit "unknown" rather than
    a blank byline."""
    note = _operator_note_verdict(verdict_id="vrd-1", text="no author")
    note["metadata"] = {"custom": {}}  # author missing
    client = _make_client(case=_case(), descendants=[note])

    notes = await fetch_operator_notes(client, "case-123")
    assert notes[0].author == "unknown"


@pytest.mark.asyncio
async def test_fetch_no_notes_returns_empty_list():
    client = _make_client(case=_case(), descendants=[])
    notes = await fetch_operator_notes(client, "case-123")
    assert notes == []


@pytest.mark.asyncio
async def test_fetch_case_not_found_raises_case_not_found_error():
    client = _make_client(case=None, case_status=404)
    with pytest.raises(CaseNotFoundError):
        await fetch_operator_notes(client, "case-missing")


@pytest.mark.asyncio
async def test_fetch_anchor_missing_raises_anchor_missing_error():
    client = _make_client(case=_case(), descendants=None, descendants_status=404)
    with pytest.raises(AnchorVerdictMissingError):
        await fetch_operator_notes(client, "case-123")


@pytest.mark.asyncio
async def test_fetch_connection_failed_raises_core_unreachable():
    client = _make_client(case=None, case_status=0)
    with pytest.raises(CoreUnreachableError):
        await fetch_operator_notes(client, "case-123")


# ------------------------------------------------------------------ #
# submit_operator_note                                                 #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_submit_constructs_operator_note_verdict():
    """Submitted verdict carries operator_note role (both subject.type
    and verdict_type for cross-producer compatibility), the full text
    in judgment.reasoning, the author in metadata.custom, and parents
    back to the case anchor."""
    client = _make_client(case=_case())

    note = await submit_operator_note(
        client, "case-123", "investigated, looks like the deploy",
        author="alice@nthlayer.com",
    )

    client.submit_verdict.assert_awaited_once()
    payload = client.submit_verdict.await_args.args[0]
    # subject.type is "custom" because operator_note isn't a valid
    # VALID_SUBJECT_TYPES value; the role lives on the typed column
    # (which to_dict emits as wire-canonical "type" since opensrm-saun.1.2).
    assert payload["subject"]["type"] == "custom"
    assert payload["type"] == "operator_note"
    assert payload["judgment"]["reasoning"] == "investigated, looks like the deploy"
    assert payload["metadata"]["custom"]["author"] == "alice@nthlayer.com"
    assert payload["parent_ids"] == ["vrd-anchor-001"]
    assert payload["service"] == "fraud-detect"
    assert payload["producer"]["system"] == "nthlayer-bench"

    assert isinstance(note, OperatorNote)
    assert note.text == "investigated, looks like the deploy"
    assert note.author == "alice@nthlayer.com"


@pytest.mark.asyncio
async def test_submit_strips_surrounding_whitespace():
    client = _make_client(case=_case())
    note = await submit_operator_note(client, "case-123", "  trimmed  \n")
    payload = client.submit_verdict.await_args.args[0]
    assert payload["judgment"]["reasoning"] == "trimmed"
    assert note.text == "trimmed"


@pytest.mark.asyncio
async def test_submit_truncates_summary_to_80_chars():
    """Long notes preserve full text in judgment.reasoning but get a
    one-line summary in subject.summary so log lines and timeline
    entries don't drown in a paragraph."""
    long_text = "a" * 200
    client = _make_client(case=_case())
    await submit_operator_note(client, "case-123", long_text)
    payload = client.submit_verdict.await_args.args[0]
    assert len(payload["subject"]["summary"]) <= 80
    assert payload["judgment"]["reasoning"] == long_text


@pytest.mark.asyncio
async def test_submit_default_author_is_operator():
    client = _make_client(case=_case())
    await submit_operator_note(client, "case-123", "no author specified")
    payload = client.submit_verdict.await_args.args[0]
    assert payload["metadata"]["custom"]["author"] == "operator"


@pytest.mark.asyncio
async def test_submit_empty_text_raises_value_error():
    """Empty / whitespace-only text is operator-input error, not a bench
    bug. Raise ValueError so the caller can treat it as input
    validation rather than a server-side problem."""
    client = _make_client(case=_case())
    with pytest.raises(ValueError):
        await submit_operator_note(client, "case-123", "")
    with pytest.raises(ValueError):
        await submit_operator_note(client, "case-123", "   \n  ")
    # No verdict was submitted.
    client.submit_verdict.assert_not_awaited()


@pytest.mark.asyncio
async def test_submit_case_not_found_raises_case_not_found_error():
    client = _make_client(case=None, case_status=404)
    with pytest.raises(CaseNotFoundError):
        await submit_operator_note(client, "case-missing", "text")


@pytest.mark.asyncio
async def test_submit_connection_failed_raises_core_unreachable():
    client = _make_client(case=_case(), submit_status=0, submit_error="connection_failed")
    with pytest.raises(CoreUnreachableError):
        await submit_operator_note(client, "case-123", "text")


@pytest.mark.asyncio
async def test_submit_other_error_raises_reasoning_capture_error():
    client = _make_client(case=_case(), submit_status=422, submit_error="missing_fields")
    with pytest.raises(ReasoningCaptureError):
        await submit_operator_note(client, "case-123", "text")


# ------------------------------------------------------------------ #
# R5 Pass 3 defensive coverage                                         #
# ------------------------------------------------------------------ #

@pytest.mark.asyncio
async def test_fetch_missing_underlying_verdict_raises_anchor_missing():
    """Defensive: a case without an ``underlying_verdict`` (data
    integrity corruption — the column is NOT NULL but a partial /
    truncated payload could lack it) raises
    :class:`AnchorVerdictMissingError`, not a naked KeyError that would
    crash the periodic refresh task."""
    bad_case = {
        "id": "case-bad",
        "service": "fraud-detect",
        "state": "pending",
        "priority": "P1",
        # no underlying_verdict
    }
    client = _make_client(case=bad_case)

    with pytest.raises(AnchorVerdictMissingError):
        await fetch_operator_notes(client, "case-bad")


@pytest.mark.asyncio
async def test_submit_missing_underlying_verdict_raises_anchor_missing():
    bad_case = {
        "id": "case-bad",
        "service": "fraud-detect",
        "state": "pending",
        "priority": "P1",
        "underlying_verdict": None,  # explicit None, not just missing
    }
    client = _make_client(case=bad_case)

    with pytest.raises(AnchorVerdictMissingError):
        await submit_operator_note(client, "case-bad", "text")


@pytest.mark.asyncio
async def test_submit_missing_service_uses_unknown_fallback():
    """Defensive: case without a ``service`` field (or with service=None)
    submits a verdict carrying ``service="unknown"`` rather than a
    falsy/empty value that downstream consumers might mishandle."""
    case_no_service = {
        "id": "case-noservice",
        "state": "pending",
        "priority": "P1",
        "underlying_verdict": "vrd-anchor-001",
        # no service
    }
    client = _make_client(case=case_no_service)

    await submit_operator_note(client, "case-noservice", "text")

    payload = client.submit_verdict.await_args.args[0]
    assert payload["service"] == "unknown"


@pytest.mark.asyncio
async def test_submit_normalises_empty_author_to_default():
    """Defensive: ``author=""`` or whitespace at submit normalises to
    DEFAULT_AUTHOR. Closes the submit/fetch consistency gap — without
    this, the submitted verdict would carry empty ``author`` while the
    fetch path normalises to ``"unknown"`` for the same row."""
    client = _make_client(case=_case())

    note_blank = await submit_operator_note(client, "case-123", "text", author="")
    blank_payload = client.submit_verdict.await_args_list[-1].args[0]
    assert blank_payload["metadata"]["custom"]["author"] == "operator"
    assert note_blank.author == "operator"

    note_ws = await submit_operator_note(client, "case-123", "text", author="   ")
    ws_payload = client.submit_verdict.await_args_list[-1].args[0]
    assert ws_payload["metadata"]["custom"]["author"] == "operator"
    assert note_ws.author == "operator"


@pytest.mark.asyncio
async def test_submit_returns_note_with_generated_verdict_id():
    """The verdict ID is generated by nthlayer_common.verdicts.create —
    the OperatorNote returned from submit carries that ID so the caller
    can immediately reference the new note in lineage operations
    without re-fetching."""
    client = _make_client(case=_case())
    note = await submit_operator_note(client, "case-123", "text")
    payload = client.submit_verdict.await_args.args[0]
    assert note.verdict_id == payload["id"]
    assert note.verdict_id.startswith("vrd-")
