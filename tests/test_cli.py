"""Tests for ``nthlayer_bench.cli`` argument validation."""
from __future__ import annotations

import pytest

from nthlayer_bench.cli import _validate_case_id


class TestValidateCaseId:
    def test_unset_is_none(self):
        assert _validate_case_id(None) is None

    def test_empty_string_is_none(self):
        assert _validate_case_id("") is None

    def test_whitespace_only_is_none(self):
        """`--case-id "   "` is operator typo territory; treat it as if
        unset rather than rendering a useless inline error."""
        assert _validate_case_id("   ") is None

    def test_strips_surrounding_whitespace(self):
        assert _validate_case_id("  case-123  ") == "case-123"

    def test_normal_case_id_passes_through(self):
        assert _validate_case_id("case-fraud-detect-001") == "case-fraud-detect-001"

    def test_forward_slash_rejected(self):
        """Path-segment delimiter would reshape the request URL when
        interpolated into f"/cases/{case_id}". Reject at the boundary."""
        with pytest.raises(SystemExit):
            _validate_case_id("../cases/foo")

    def test_question_mark_rejected(self):
        """Query delimiter would split the path into path + query."""
        with pytest.raises(SystemExit):
            _validate_case_id("case?x=1")

    def test_hash_rejected(self):
        """Fragment delimiter."""
        with pytest.raises(SystemExit):
            _validate_case_id("case#frag")

    def test_leading_double_dot_rejected(self):
        """Even without slashes, a leading '..' is path-traversal-shaped
        and rejected to keep operator intent unambiguous."""
        with pytest.raises(SystemExit):
            _validate_case_id("..foo")
