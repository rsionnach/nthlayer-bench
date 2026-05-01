"""Textual widgets for the bench TUI. Compose with ``BenchApp``."""

from nthlayer_bench.widgets.case_bench import CaseBenchPanel
from nthlayer_bench.widgets.case_brief import CaseBriefPanel
from nthlayer_bench.widgets.case_review import CaseReviewPanel
from nthlayer_bench.widgets.reasoning_capture import ReasoningCapturePanel
from nthlayer_bench.widgets.situation_board import SituationBoardPanel

__all__ = [
    "CaseBenchPanel",
    "CaseBriefPanel",
    "CaseReviewPanel",
    "ReasoningCapturePanel",
    "SituationBoardPanel",
]
