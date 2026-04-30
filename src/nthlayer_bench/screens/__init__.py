"""Textual screens for the bench TUI.

A screen owns a full-viewport view: situation board, case bench, case
detail. Widgets compose into screens; the app pushes/pops screens to
navigate. Keeping screens out of ``widgets/`` makes navigation routes
easy to find.
"""

from nthlayer_bench.screens.case_detail import CaseDetailScreen

__all__ = ["CaseDetailScreen"]
