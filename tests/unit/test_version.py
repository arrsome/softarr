"""Unit tests for the version comparison utility."""

from softarr.utils.version import _version_tuple, compare_versions


class TestVersionTuple:
    def test_three_component(self):
        assert _version_tuple("26.2.1") == (26, 2, 1)

    def test_v_prefix(self):
        assert _version_tuple("v7.6.1") == (7, 6, 1)

    def test_four_component(self):
        assert _version_tuple("25.8.4.2") == (25, 8, 4, 2)

    def test_unknown(self):
        assert _version_tuple("unknown") == (0,)

    def test_empty(self):
        assert _version_tuple("") == (0,)

    def test_single_component(self):
        assert _version_tuple("3") == (3,)

    def test_stops_at_non_numeric(self):
        assert _version_tuple("1.2.3-beta") == (1, 2, 3)


class TestCompareVersions:
    def test_newer_greater(self):
        assert compare_versions("26.2.1", "7.6.1") == 1

    def test_older_lesser(self):
        assert compare_versions("7.6.1", "26.2.1") == -1

    def test_equal(self):
        assert compare_versions("25.8.4.2", "25.8.4.2") == 0

    def test_more_components_wins(self):
        # 25.8.4.2 vs 25.8.4 -- more components means newer
        assert compare_versions("25.8.4.2", "25.8.4") == 1

    def test_unknown_is_oldest(self):
        assert compare_versions("unknown", "1.0.0") == -1

    def test_v_prefix_handled(self):
        assert compare_versions("v7.6.2", "v7.6.1") == 1
