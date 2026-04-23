"""Tests for the release comparison logic (build_compare_response helper)."""

from datetime import datetime, timezone
from uuid import uuid4

from softarr.api.v1.releases import build_compare_response
from softarr.models.release import FlagStatus, TrustStatus, WorkflowState
from softarr.schemas.release import ReleaseResponse


def _make_release(**overrides) -> ReleaseResponse:
    """Build a minimal ReleaseResponse for testing."""
    defaults = dict(
        id=uuid4(),
        software_id=uuid4(),
        name="MyApp 1.0",
        version="1.0.0",
        supported_os=["windows"],
        architecture="x64",
        publisher="ACME Corp",
        source_type="github",
        source_origin=None,
        confidence_score=0.9,
        trust_status=TrustStatus.UNVERIFIED,
        flag_status=FlagStatus.NONE,
        workflow_state=WorkflowState.DISCOVERED,
        workflow_changed_at=None,
        workflow_changed_by=None,
        flag_reasons=[],
        unusual_files=[],
        suspicious_patterns=[],
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        software_name="MyApp",
    )
    defaults.update(overrides)
    return ReleaseResponse(**defaults)


class TestBuildCompareResponse:
    def test_identical_releases_produce_no_diffs(self):
        ra = _make_release()
        rb = _make_release(id=uuid4())
        result = build_compare_response(ra, rb)
        assert result.differences == []

    def test_identical_releases_newer_version_equal(self):
        ra = _make_release()
        rb = _make_release(id=uuid4())
        result = build_compare_response(ra, rb)
        assert result.newer_version == "equal"

    def test_identical_releases_recommendation_mentions_equivalent(self):
        ra = _make_release()
        rb = _make_release(id=uuid4())
        result = build_compare_response(ra, rb)
        assert "equivalent" in result.recommendation.lower()

    def test_version_diff_detected_in_diffs(self):
        ra = _make_release(version="2.0.0")
        rb = _make_release(version="1.0.0", id=uuid4())
        result = build_compare_response(ra, rb)
        version_diff = next(
            (d for d in result.differences if d.field == "version"), None
        )
        assert version_diff is not None
        assert version_diff.a_value == "2.0.0"
        assert version_diff.b_value == "1.0.0"

    def test_newer_version_a_when_a_is_higher(self):
        ra = _make_release(version="2.0.0")
        rb = _make_release(version="1.0.0", id=uuid4())
        result = build_compare_response(ra, rb)
        assert result.newer_version == "a"

    def test_newer_version_b_when_b_is_higher(self):
        ra = _make_release(version="1.0.0")
        rb = _make_release(version="2.0.0", id=uuid4())
        result = build_compare_response(ra, rb)
        assert result.newer_version == "b"

    def test_equal_versions_produces_equal(self):
        ra = _make_release(version="1.5.3")
        rb = _make_release(version="1.5.3", id=uuid4())
        result = build_compare_response(ra, rb)
        assert result.newer_version == "equal"

    def test_confidence_score_diff_detected(self):
        ra = _make_release(confidence_score=0.9)
        rb = _make_release(confidence_score=0.6, id=uuid4())
        result = build_compare_response(ra, rb)
        diff = next(
            (d for d in result.differences if d.field == "confidence_score"), None
        )
        assert diff is not None
        assert diff.a_value == 0.9
        assert diff.b_value == 0.6

    def test_recommendation_favours_higher_confidence_a(self):
        ra = _make_release(confidence_score=0.9)
        rb = _make_release(confidence_score=0.5, id=uuid4())
        result = build_compare_response(ra, rb)
        assert "Release A has a higher confidence score" in result.recommendation

    def test_recommendation_favours_higher_confidence_b(self):
        ra = _make_release(confidence_score=0.5)
        rb = _make_release(confidence_score=0.9, id=uuid4())
        result = build_compare_response(ra, rb)
        assert "Release B has a higher confidence score" in result.recommendation

    def test_flag_status_diff_detected(self):
        ra = _make_release(flag_status=FlagStatus.NONE)
        rb = _make_release(flag_status=FlagStatus.WARNING, id=uuid4())
        result = build_compare_response(ra, rb)
        diff = next((d for d in result.differences if d.field == "flag_status"), None)
        assert diff is not None
        assert diff.a_value == "none"
        assert diff.b_value == "warning"

    def test_recommendation_favours_unflagged_a(self):
        ra = _make_release(flag_status=FlagStatus.NONE)
        rb = _make_release(flag_status=FlagStatus.WARNING, id=uuid4())
        result = build_compare_response(ra, rb)
        assert "Release A has no flags" in result.recommendation

    def test_recommendation_favours_unflagged_b(self):
        ra = _make_release(flag_status=FlagStatus.WARNING)
        rb = _make_release(flag_status=FlagStatus.NONE, id=uuid4())
        result = build_compare_response(ra, rb)
        assert "Release B has no flags" in result.recommendation

    def test_flag_reasons_diff_detected(self):
        ra = _make_release(flag_reasons=["suspicious_name"])
        rb = _make_release(flag_reasons=[], id=uuid4())
        result = build_compare_response(ra, rb)
        diff = next((d for d in result.differences if d.field == "flag_reasons"), None)
        assert diff is not None

    def test_fewer_flag_reasons_favours_b(self):
        ra = _make_release(flag_reasons=["suspicious_name", "unsigned"])
        rb = _make_release(flag_reasons=[], id=uuid4())
        result = build_compare_response(ra, rb)
        assert "Release B has fewer flag reasons" in result.recommendation

    def test_enum_values_normalised_in_diffs(self):
        """Enum instances should be normalised to their string values in diff output."""
        ra = _make_release(workflow_state=WorkflowState.DISCOVERED)
        rb = _make_release(workflow_state=WorkflowState.APPROVED, id=uuid4())
        result = build_compare_response(ra, rb)
        diff = next(
            (d for d in result.differences if d.field == "workflow_state"), None
        )
        assert diff is not None
        assert diff.a_value == "discovered"
        assert diff.b_value == "approved"

    def test_response_includes_both_releases(self):
        ra = _make_release(version="1.0.0")
        rb = _make_release(version="2.0.0", id=uuid4())
        result = build_compare_response(ra, rb)
        assert result.release_a.version == "1.0.0"
        assert result.release_b.version == "2.0.0"

    def test_recommendation_mentions_newer_version(self):
        ra = _make_release(version="2.0.0")
        rb = _make_release(version="1.0.0", id=uuid4())
        result = build_compare_response(ra, rb)
        assert "Release A is the newer version" in result.recommendation
