"""SRE-facing logic modules for nthlayer-bench.

Pure async functions and dataclasses operating against ``CoreAPIClient``.
Free of UI dependencies so tests, future CLI wrappers, and HTTP handlers
can import them without dragging Textual in.
"""

from nthlayer_bench.sre.brief import (
    AnchorVerdictMissingError,
    BriefError,
    BriefState,
    CaseNotFoundError,
    CoreUnreachableError,
    PagingBrief,
    build_paging_brief,
    render_brief,
)
from nthlayer_bench.sre.escalation import (
    DEFAULT_POLL_LIMIT,
    ESCALATION_SEVERITIES,
    EscalationEvent,
    EscalationMonitor,
)
from nthlayer_bench.sre.case_bench import (
    PRIORITY_ORDER,
    CaseBenchError,
    CaseBenchView,
    CaseSummary,
    fetch_case_bench,
    render_case_bench,
)
from nthlayer_bench.sre.reasoning_capture import (
    DEFAULT_AUTHOR,
    OperatorNote,
    ReasoningCaptureError,
    build_operator_note_verdict,
    fetch_case,
    fetch_operator_notes,
    operator_note_from_verdict,
    submit_operator_note,
    submit_operator_note_verdict,
)
from nthlayer_bench.sre.write_queue import (
    DrainResult,
    PendingNote,
    WriteQueue,
)
from nthlayer_bench.sre.post_incident import (
    PostIncidentError,
    PostIncidentReview,
    ReviewState,
    TimelineEntry,
    VerdictAccuracy,
    build_post_incident_review,
    render_post_incident_review,
)
from nthlayer_bench.sre.situation_board import (
    BreachEvent,
    PortfolioSnapshot,
    SituationBoardError,
    SituationBoardView,
    fetch_situation_board,
    render_situation_board,
)

__all__ = [
    # brief
    "AnchorVerdictMissingError",
    "BriefError",
    "BriefState",
    "CaseNotFoundError",
    "CoreUnreachableError",
    "PagingBrief",
    "build_paging_brief",
    "render_brief",
    # case bench
    "PRIORITY_ORDER",
    "CaseBenchError",
    "CaseBenchView",
    "CaseSummary",
    "fetch_case_bench",
    "render_case_bench",
    # post-incident
    "PostIncidentError",
    "PostIncidentReview",
    "ReviewState",
    "TimelineEntry",
    "VerdictAccuracy",
    "build_post_incident_review",
    "render_post_incident_review",
    # situation board
    "BreachEvent",
    "PortfolioSnapshot",
    "SituationBoardError",
    "SituationBoardView",
    "fetch_situation_board",
    "render_situation_board",
    # reasoning capture
    "DEFAULT_AUTHOR",
    "OperatorNote",
    "ReasoningCaptureError",
    "build_operator_note_verdict",
    "fetch_case",
    "fetch_operator_notes",
    "operator_note_from_verdict",
    "submit_operator_note",
    "submit_operator_note_verdict",
    # write queue
    "DrainResult",
    "PendingNote",
    "WriteQueue",
    # escalation
    "DEFAULT_POLL_LIMIT",
    "ESCALATION_SEVERITIES",
    "EscalationEvent",
    "EscalationMonitor",
]
