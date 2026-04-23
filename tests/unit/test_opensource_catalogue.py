"""Tests for the built-in open-source software catalogue."""

from softarr.data.opensource_catalogue import CATALOGUE

_REQUIRED_FIELDS = {
    "canonical_name",
    "aliases",
    "expected_publisher",
    "supported_os",
    "notes",
    "tags",
    "source_preferences",
    "monitored",
    "download_profile",
    "version_format_rules",
    "auto_reject_rules",
    "release_type_filter",
}

_VALID_OS = {"windows", "linux", "macos"}


class TestCatalogue:
    def test_has_20_entries(self):
        assert len(CATALOGUE) == 20

    def test_all_have_canonical_name(self):
        for entry in CATALOGUE:
            assert entry.get("canonical_name"), f"Missing canonical_name: {entry}"

    def test_canonical_names_unique(self):
        names = [e["canonical_name"] for e in CATALOGUE]
        assert len(names) == len(set(names)), "Duplicate canonical names found"

    def test_all_have_open_source_tag(self):
        for entry in CATALOGUE:
            assert "open-source" in entry.get("tags", []), (
                f"{entry['canonical_name']} missing 'open-source' tag"
            )

    def test_all_have_supported_os(self):
        for entry in CATALOGUE:
            os_list = entry.get("supported_os", [])
            assert len(os_list) > 0, f"{entry['canonical_name']} has no supported_os"

    def test_supported_os_values_valid(self):
        for entry in CATALOGUE:
            for os_val in entry.get("supported_os", []):
                assert os_val in _VALID_OS, (
                    f"{entry['canonical_name']} has unknown OS value: {os_val}"
                )

    def test_all_have_source_preferences(self):
        for entry in CATALOGUE:
            prefs = entry.get("source_preferences", [])
            assert len(prefs) > 0, (
                f"{entry['canonical_name']} has no source_preferences"
            )

    def test_all_required_fields_present(self):
        for entry in CATALOGUE:
            missing = _REQUIRED_FIELDS - set(entry.keys())
            assert not missing, f"{entry['canonical_name']} missing fields: {missing}"

    def test_monitored_is_true_for_all(self):
        for entry in CATALOGUE:
            assert entry.get("monitored") is True, (
                f"{entry['canonical_name']} should have monitored=True"
            )

    def test_aliases_are_lists(self):
        for entry in CATALOGUE:
            assert isinstance(entry.get("aliases", []), list), (
                f"{entry['canonical_name']} aliases must be a list"
            )

    def test_tags_are_lists(self):
        for entry in CATALOGUE:
            assert isinstance(entry.get("tags", []), list), (
                f"{entry['canonical_name']} tags must be a list"
            )

    def test_notes_non_empty(self):
        for entry in CATALOGUE:
            notes = entry.get("notes") or ""
            assert len(notes) > 0, f"{entry['canonical_name']} has empty notes"

    def test_no_payment_required_apps(self):
        """Ensure none of the apps in the catalogue are paid/proprietary."""
        # These are known-open-source apps -- just verify the tags don't include
        # anything suggesting payment
        for entry in CATALOGUE:
            tags = entry.get("tags", [])
            assert "paid" not in tags, f"{entry['canonical_name']} tagged as paid"
            assert "proprietary" not in tags, (
                f"{entry['canonical_name']} tagged as proprietary"
            )
