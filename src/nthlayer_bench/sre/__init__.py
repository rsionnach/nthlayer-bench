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

__all__ = [
    "AnchorVerdictMissingError",
    "BriefError",
    "BriefState",
    "CaseNotFoundError",
    "CoreUnreachableError",
    "PagingBrief",
    "build_paging_brief",
    "render_brief",
]
