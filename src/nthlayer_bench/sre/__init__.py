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
from nthlayer_bench.sre.case_bench import (
    PRIORITY_ORDER,
    CaseBenchError,
    CaseBenchView,
    CaseSummary,
    fetch_case_bench,
    render_case_bench,
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
]
