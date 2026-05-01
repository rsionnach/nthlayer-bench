"""Reasoning capture — operator notes attached to a case.

The first write path in bench. Operators select a case, type their
reasoning ("investigated, looks like the deploy"), and submit; the
note is persisted to core as an ``operator_note`` verdict linked back
to the case's anchor (``underlying_verdict``). Past notes are fetched
on each refresh so all operators viewing the case see the trail.

Verdict shape:

- ``subject.type = "custom"`` — ``operator_note`` is not in
  ``VALID_SUBJECT_TYPES`` (the model's enum), so the role lives on
  the typed ``verdict_type`` column instead. ``custom`` is the
  validated subject type for verdicts whose role-shape is
  producer-specific rather than one of the canonical agent roles.
- ``verdict_type = "operator_note"`` — RBAC §10's typed taxonomy
  (in ``VALID_VERDICT_TYPES``); core stores it on the typed column
  for query-time filtering. Reads use dual-path detection
  (``subject.type`` OR ``verdict_type``) so legacy producers that
  encoded the role on ``subject.type`` are also recognised.
- ``parent_ids = [case.underlying_verdict]`` — every note hangs off
  the case anchor (parallel siblings rather than a serial chain) so
  they show up cleanly via ``get_descendants``.
- ``service`` — copied from the case so per-service queries surface
  the notes alongside other service-tagged verdicts.
- ``judgment.action = "flag"``, ``judgment.confidence = 1.0`` —
  operators are recording observations, not making predictions, so
  confidence is full and the action is the neutral "flag".
- ``metadata.custom.author`` — operator identity. Defaults to
  ``"operator"`` at the SRE-logic layer; the widget passes through
  whatever the app/CLI configures.

Spec: opensrm-81rn.4 (case detail with reasoning capture).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from nthlayer_common.api_client import APIResult, CoreAPIClient
from nthlayer_common.verdicts import Verdict, create as verdict_create
from nthlayer_common.verdicts.serialise import to_dict

from nthlayer_bench.sre.brief import (
    AnchorVerdictMissingError,
    BriefError,
    CaseNotFoundError,
    CoreUnreachableError,
)


@dataclass
class OperatorNote:
    """One operator-authored note attached to a case."""

    verdict_id: str
    case_id: str
    author: str
    text: str
    created_at: str  # ISO 8601


class ReasoningCaptureError(BriefError):
    """Raised when reasoning capture cannot read or write. Inherits
    :class:`BriefError` so the case-detail screen catches all SRE error
    paths through one filter."""


__all__ = [
    "OperatorNote",
    "ReasoningCaptureError",
    "AnchorVerdictMissingError",
    "CaseNotFoundError",
    "CoreUnreachableError",
    "fetch_case",
    "fetch_operator_notes",
    "submit_operator_note",
    "build_operator_note_verdict",
    "submit_operator_note_verdict",
    "operator_note_from_verdict",
]


async def fetch_case(client: CoreAPIClient, case_id: str) -> dict:
    """Fetch a case dict from core.

    Public companion to :func:`fetch_operator_notes` so callers (e.g.
    the reasoning-capture panel) can cache the case for offline-submit
    via the write queue without piggy-backing on a private helper.
    """
    return await _get_case(client, case_id)


# ------------------------------------------------------------------ #
# Read path                                                            #
# ------------------------------------------------------------------ #

async def fetch_operator_notes(
    client: CoreAPIClient,
    case_id: str,
) -> list[OperatorNote]:
    """Fetch operator notes attached to a case, oldest first.

    Walks descendants of ``case["underlying_verdict"]`` and filters for
    verdicts whose role is ``operator_note``. Same lineage anchor as the
    brief and post-incident review so all three surfaces operate on a
    consistent view of the case's chain.
    """
    case = await _get_case(client, case_id)
    anchor_id = _require_anchor_id(case, case_id)
    descendants = await _get_descendants(client, anchor_id)

    notes: list[OperatorNote] = []
    for v in descendants:
        if not _is_operator_note(v):
            continue
        notes.append(_to_operator_note(v, case_id))

    notes.sort(key=lambda n: (n.created_at, n.verdict_id))
    return notes


def _is_operator_note(verdict: dict) -> bool:
    """Detect operator-note verdicts robustly across producers — match
    on either the legacy role-string ``subject.type`` or the typed
    ``verdict_type`` column. Mirrors :func:`_is_outcome_resolution`'s
    dual-path detection in post_incident.py."""
    if (verdict.get("subject") or {}).get("type") == "operator_note":
        return True
    return verdict.get("verdict_type") == "operator_note"


def _to_operator_note(verdict: dict, case_id: str) -> OperatorNote:
    judgment = verdict.get("judgment") or {}
    metadata = verdict.get("metadata") or {}
    custom = metadata.get("custom") or {}
    author = custom.get("author")
    return OperatorNote(
        verdict_id=verdict.get("id", ""),
        case_id=case_id,
        author=author if isinstance(author, str) and author else "unknown",
        text=judgment.get("reasoning", ""),
        created_at=verdict.get("created_at", ""),
    )


# ------------------------------------------------------------------ #
# Write path                                                           #
# ------------------------------------------------------------------ #

DEFAULT_AUTHOR = "operator"


def build_operator_note_verdict(
    case: dict,
    text: str,
    *,
    author: str = DEFAULT_AUTHOR,
) -> Verdict:
    """Construct an ``operator_note`` verdict for a given case dict.

    Pure: no I/O. Generates a stable verdict ID at construction time so
    a queued retry under a write queue (Bead 9b) re-submits the SAME
    ID core saw on the first attempt — letting core return 409 on the
    duplicate and the queue drop the entry as already-accepted.

    Used by both the synchronous :func:`submit_operator_note` (which
    fetches the case first) and the write queue (which builds verdicts
    eagerly from the panel's cached case data).

    Raises :class:`ValueError` for empty/whitespace text and
    :class:`AnchorVerdictMissingError` when the case lacks
    ``underlying_verdict``.
    """
    stripped = text.strip()
    if not stripped:
        raise ValueError("operator note text must not be empty")

    # Normalise author at the build boundary: empty/whitespace falls
    # back to DEFAULT_AUTHOR. Without this, submit would write "" while
    # the fetch path's _to_operator_note normalises empty → "unknown" —
    # the same row would have two different author values across
    # submit-then-fetch round trips.
    normalised_author = author.strip() or DEFAULT_AUTHOR

    case_id = case.get("id", "")
    anchor_id = _require_anchor_id(case, case_id)
    service = case.get("service") or "unknown"

    verdict = verdict_create(
        subject={
            "type": "custom",
            "ref": case_id,
            "summary": _summarise(stripped),
        },
        judgment={
            "action": "flag",
            "confidence": 1.0,
            "reasoning": stripped,
        },
        producer={"system": "nthlayer-bench", "instance": "operator"},
        metadata={"custom": {"author": normalised_author}},
    )
    verdict.parent_ids = [anchor_id]
    verdict.verdict_type = "operator_note"
    verdict.service = service
    return verdict


async def submit_operator_note_verdict(
    client: CoreAPIClient, verdict: Verdict
) -> APIResult:
    """Submit a pre-built operator-note verdict to core.

    Returns the raw :class:`APIResult` so callers can branch on
    ``status_code == 409`` (already accepted, drop from queue),
    ``status_code == 0`` (connection failed, keep queued), or other
    non-2xx (treated as transient by the queue today; downstream may
    later distinguish 4xx-permanent from 5xx-transient if operator
    feedback warrants).
    """
    return await client.submit_verdict(to_dict(verdict))


def operator_note_from_verdict(verdict: Verdict, case_id: str) -> OperatorNote:
    """Project a built/submitted Verdict back into an OperatorNote for
    immediate UI rendering. Used after a successful submit and after
    a successful queued-write replay."""
    custom = verdict.metadata.custom if verdict.metadata else {}
    author = custom.get("author") if isinstance(custom, dict) else None
    return OperatorNote(
        verdict_id=verdict.id,
        case_id=case_id,
        author=author if isinstance(author, str) and author else DEFAULT_AUTHOR,
        text=verdict.judgment.reasoning,
        created_at=verdict.timestamp.isoformat(),
    )


async def submit_operator_note(
    client: CoreAPIClient,
    case_id: str,
    text: str,
    *,
    author: str = DEFAULT_AUTHOR,
) -> OperatorNote:
    """High-level write path: fetch the case, build the verdict,
    submit it, and project the result into an :class:`OperatorNote`.

    Empty or whitespace-only ``text`` raises :class:`ValueError` —
    callers should guard at the input layer. The submitted verdict
    carries the full text in ``judgment.reasoning`` and a truncated
    summary in ``subject.summary`` so the operator's wording survives
    intact for the audit trail.

    For the queue-friendly two-phase variant (fetch case once, build
    eagerly, submit later), use :func:`build_operator_note_verdict`
    + :func:`submit_operator_note_verdict` directly.
    """
    case = await _get_case(client, case_id)
    verdict = build_operator_note_verdict(case, text, author=author)
    result = await submit_operator_note_verdict(client, verdict)

    if result.ok:
        return operator_note_from_verdict(verdict, case_id)
    if result.status_code == 0:
        raise CoreUnreachableError(result.detail or {"error": result.error})
    raise ReasoningCaptureError(
        f"submit_verdict failed: {result.error} (status={result.status_code})"
    )


def _summarise(text: str, *, max_len: int = 80) -> str:
    """First 80 chars of the note as a one-line summary. Carries enough
    signal for log lines and timeline entries without inlining a full
    paragraph."""
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


# ------------------------------------------------------------------ #
# Shared fetch helpers (mirror brief.py's shape)                       #
# ------------------------------------------------------------------ #

def _require_anchor_id(case: dict, case_id: str) -> str:
    """Extract the case's underlying-verdict anchor or raise an
    operator-friendly error.

    Defensive: a case missing or carrying ``None`` for
    ``underlying_verdict`` is a data-integrity issue (the case schema
    declares it ``NOT NULL``, so this can only happen on a corrupt
    payload or schema drift). Surfacing it as
    :class:`AnchorVerdictMissingError` lets the widget render the
    inline data-integrity error rather than crashing the periodic
    refresh task with an opaque ``KeyError``.
    """
    anchor_id = case.get("underlying_verdict")
    if not anchor_id:
        raise AnchorVerdictMissingError(case_id)
    return anchor_id


async def _get_case(client: CoreAPIClient, case_id: str) -> dict[str, Any]:
    result = await client.get_case(case_id)
    if result.ok:
        return result.data
    if result.status_code == 404:
        raise CaseNotFoundError(case_id)
    if result.status_code == 0:
        raise CoreUnreachableError(result.detail or {"error": result.error})
    raise ReasoningCaptureError(
        f"get_case failed: {result.error} (status={result.status_code})"
    )


async def _get_descendants(client: CoreAPIClient, anchor_id: str) -> list[dict]:
    result = await client.get_descendants(anchor_id)
    if result.ok:
        return result.data or []
    if result.status_code == 404:
        # Anchor missing — case carries underlying_verdict but core has
        # no record. Surface explicitly so the widget can render the
        # data-integrity error rather than an empty notes list.
        raise AnchorVerdictMissingError(anchor_id)
    if result.status_code == 0:
        raise CoreUnreachableError(result.detail or {"error": result.error})
    raise ReasoningCaptureError(
        f"get_descendants failed: {result.error} (status={result.status_code})"
    )
