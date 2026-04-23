"""Unit tests for search_filter_service."""

import pytest

from softarr.services.search_filter_service import (
    VALID_MODES,
    apply_boolean,
    apply_exact,
    apply_fuzzy,
    apply_regex,
    filter_results,
)


def _r(name: str) -> dict:
    """Build a minimal result dict for testing."""
    return {"name": name, "display_name": name, "raw_data": {}}


RESULTS = [
    _r("LibreOffice_26.2.1_Win_x86-64"),
    _r("VLC_media_player_3.0.21_Win64"),
    _r("Firefox_130.0_Setup"),
    _r("Notepad++_8.6_Installer"),
]


class TestApplyRegex:
    def test_matches_pattern(self):
        out = apply_regex(RESULTS, r"libre.*win")
        assert len(out) == 1
        assert "LibreOffice" in out[0]["name"]

    def test_case_insensitive(self):
        out = apply_regex(RESULTS, r"FIREFOX")
        assert len(out) == 1

    def test_no_matches(self):
        out = apply_regex(RESULTS, r"^xyz_nonexistent")
        assert out == []

    def test_all_match(self):
        out = apply_regex(RESULTS, r"\d+\.\d+")
        assert len(out) == len(RESULTS)

    def test_invalid_pattern_raises(self):
        with pytest.raises(ValueError, match="Invalid regex"):
            apply_regex(RESULTS, r"[invalid")


class TestApplyFuzzy:
    def test_close_match(self):
        # "libr" is similar enough to "LibreOffice_26.2.1_Win_x86-64"
        out = apply_fuzzy(RESULTS, "libre", threshold=0.2)
        assert any("LibreOffice" in r["name"] for r in out)

    def test_empty_query_returns_all(self):
        out = apply_fuzzy(RESULTS, "")
        assert len(out) == len(RESULTS)

    def test_very_strict_threshold_limits_results(self):
        out = apply_fuzzy(RESULTS, "LibreOffice", threshold=0.9)
        # With threshold 0.9 very few results should match
        assert len(out) <= 2


class TestApplyExact:
    def test_substring_match(self):
        out = apply_exact(RESULTS, "vlc")
        assert len(out) == 1
        assert "VLC" in out[0]["name"]

    def test_case_insensitive(self):
        out = apply_exact(RESULTS, "FIREFOX")
        assert len(out) == 1

    def test_empty_query_returns_all(self):
        out = apply_exact(RESULTS, "")
        assert len(out) == len(RESULTS)

    def test_no_match(self):
        out = apply_exact(RESULTS, "totallynotpresent")
        assert out == []


class TestApplyBoolean:
    def test_and_operator(self):
        out = apply_boolean(RESULTS, "Win AND 26")
        assert all(
            "Win" in r["name"].lower() or "win" in r["name"].lower() for r in out
        )

    def test_not_operator(self):
        out = apply_boolean(RESULTS, "Setup NOT Notepad")
        # "Firefox_130.0_Setup" should match but not "Notepad"
        names = [r["name"] for r in out]
        assert any("Firefox" in n for n in names)
        assert not any("Notepad" in n for n in names)

    def test_or_operator(self):
        out = apply_boolean(RESULTS, "VLC OR LibreOffice")
        assert len(out) == 2

    def test_empty_expr_returns_all(self):
        out = apply_boolean(RESULTS, "")
        assert len(out) == len(RESULTS)


class TestFilterResults:
    def test_standard_mode_returns_unchanged(self):
        out = filter_results(RESULTS, "standard", "vlc")
        # Standard mode ignores the query
        assert len(out) == len(RESULTS)

    def test_invalid_mode_treated_as_standard(self):
        out = filter_results(RESULTS, "nonsense", "vlc")
        assert len(out) == len(RESULTS)

    def test_exact_mode(self):
        out = filter_results(RESULTS, "exact", "vlc")
        assert len(out) == 1

    def test_regex_mode(self):
        out = filter_results(RESULTS, "regex", r"firefox|vlc")
        assert len(out) == 2

    def test_valid_modes_set(self):
        assert "standard" in VALID_MODES
        assert "regex" in VALID_MODES
        assert "fuzzy" in VALID_MODES
        assert "exact" in VALID_MODES
        assert "boolean" in VALID_MODES
