"""Unit tests for .github/scripts/detect_bump_type.py."""

import sys
from pathlib import Path

# Import the script directly -- it lives outside src/ so add it to path.
sys.path.insert(0, str(Path(__file__).parent.parent.parent / ".github" / "scripts"))

from detect_bump_type import detect  # noqa: E402


class TestDetectBumpType:
    def test_breaking_change_is_major(self):
        assert detect(["Add feature X", "BREAKING CHANGE: removed old API"]) == "major"

    def test_major_prefix_is_major(self):
        assert detect(["major: redesign auth system"]) == "major"

    def test_add_keyword_is_minor(self):
        assert detect(["Add contributor service"]) == "minor"

    def test_implement_keyword_is_minor(self):
        assert detect(["Implement PWA support"]) == "minor"

    def test_new_keyword_is_minor(self):
        assert detect(["New search filter modes"]) == "minor"

    def test_fix_keyword_is_patch(self):
        assert detect(["Fix about page 500 error"]) == "patch"

    def test_refactor_is_patch(self):
        assert detect(["Refactor staging queue"]) == "patch"

    def test_mixed_fix_and_feature_is_minor(self):
        # Minor wins over patch
        assert detect(["Fix login redirect", "Add push notifications"]) == "minor"

    def test_major_beats_minor(self):
        assert (
            detect(["Add feature", "BREAKING CHANGE: dropped Python 3.10"]) == "major"
        )

    def test_empty_is_patch(self):
        assert detect([]) == "patch"

    def test_skips_bump_version_commits(self):
        assert detect(["Bump version to 1.1.0", "Update changelog"]) == "patch"

    def test_skips_merge_commits(self):
        assert detect(["Merge pull request #12 from user/branch"]) == "patch"

    def test_case_insensitive(self):
        assert detect(["ADD search modes"]) == "minor"
        assert detect(["FIX login bug"]) == "patch"
