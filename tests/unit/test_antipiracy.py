"""Tests for the anti-piracy keyword scanner."""

from softarr.analysis.antipiracy import check_release_for_piracy, scan_for_piracy


class TestScanForPiracy:
    def test_no_match_returns_empty(self):
        assert scan_for_piracy("LibreOffice 7.6.4") == []

    def test_detects_cracked(self):
        assert scan_for_piracy("LibreOffice-cracked.exe") != []

    def test_detects_keygen(self):
        assert scan_for_piracy("app-keygen.zip") != []

    def test_detects_keygen_variant(self):
        assert scan_for_piracy("key-generator") != []

    def test_detects_warez(self):
        assert scan_for_piracy("warez.app") != []

    def test_detects_nulled(self):
        assert scan_for_piracy("MyApp nulled") != []

    def test_case_insensitive(self):
        assert scan_for_piracy("CRACKED EDITION") != []

    def test_detects_pirated(self):
        assert scan_for_piracy("pirated software 2025") != []

    def test_normal_release_name_passes(self):
        assert scan_for_piracy("Firefox 124.0 Setup.exe") == []

    def test_normal_version_with_numbers_passes(self):
        assert scan_for_piracy("blender-4.0.2-linux-x64.tar.xz") == []


class TestCheckReleaseForPiracy:
    def test_clean_release(self):
        result = check_release_for_piracy(
            "LibreOffice 7.6.4", ["LibreOffice_7.6.4_Win_x86_64.msi"]
        )
        assert result == []

    def test_match_in_name(self):
        result = check_release_for_piracy("MyApp-cracked", [])
        assert len(result) > 0

    def test_match_in_asset_names(self):
        result = check_release_for_piracy(
            "MyApp 1.0", ["myapp-keygen.exe", "readme.txt"]
        )
        assert len(result) > 0

    def test_deduplicates_results(self):
        result = check_release_for_piracy("cracked app", ["cracked.zip"])
        # Both name and asset contain 'cracked' -- should deduplicate
        assert result.count("cracked") == 1

    def test_multiple_keywords_detected(self):
        result = check_release_for_piracy("cracked warez", [])
        assert len(result) >= 2
