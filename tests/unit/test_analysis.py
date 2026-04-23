from softarr.analysis.engine import AnalysisEngine
from softarr.analysis.hash import check_hash
from softarr.analysis.signature import check_signature
from softarr.analysis.suspicious import (
    detect_suspicious_in_list,
    detect_suspicious_patterns,
)
from softarr.models.release import FlagStatus
from softarr.utils.helpers import (
    calculate_overall_risk,
    is_suspicious_filename,
    normalize_version,
)


class TestSuspiciousFilename:
    def test_clean_filename(self):
        assert is_suspicious_filename("setup.exe") is False

    def test_crack_detected(self):
        assert is_suspicious_filename("crack.exe") is True

    def test_keygen_detected(self):
        assert is_suspicious_filename("keygen_v2.zip") is True

    def test_case_insensitive(self):
        assert is_suspicious_filename("CRACK.EXE") is True

    def test_normal_installer(self):
        assert is_suspicious_filename("MyApp-Setup-1.0.0.msi") is False


class TestSuspiciousPatterns:
    def test_empty_filename(self):
        assert detect_suspicious_patterns("") == []

    def test_crack_pattern(self):
        result = detect_suspicious_patterns("app-crack-v2.exe")
        assert "crack" in result

    def test_multiple_patterns(self):
        result = detect_suspicious_in_list(["crack.exe", "keygen.bat", "readme.txt"])
        assert "crack" in result
        assert "keygen" in result

    def test_patch_excludes_patchnotes(self):
        result = detect_suspicious_patterns("patchnotes.txt")
        assert len(result) == 0


class TestSignatureCheck:
    def test_matching_publisher(self):
        result = check_signature(
            {
                "publisher": "Microsoft",
                "expected_publisher": "Microsoft",
            }
        )
        assert result == "valid"

    def test_no_publisher(self):
        result = check_signature({})
        assert result == "not_signed"

    def test_mismatched_publisher(self):
        result = check_signature(
            {
                "publisher": "Unknown",
                "expected_publisher": "Microsoft",
            }
        )
        assert result == "not_signed"


class TestHashCheck:
    def test_unknown_no_hashes(self):
        result = check_hash({})
        assert result == "unknown"

    def test_unknown_no_computed(self):
        result = check_hash({"known_hashes": {"sha256": "abc123"}})
        assert result == "unknown"

    def test_match(self):
        result = check_hash(
            {
                "known_hashes": {"sha256": "abc123"},
                "computed_hash": "abc123",
            }
        )
        assert result == "match"

    def test_mismatch(self):
        result = check_hash(
            {
                "known_hashes": {"sha256": "abc123"},
                "computed_hash": "xyz789",
            }
        )
        assert result == "mismatch"


class TestNormalizeVersion:
    def test_strip_v_prefix(self):
        assert normalize_version("v1.2.3") == "1.2.3"

    def test_strip_non_numeric(self):
        assert normalize_version("v1.2.3-beta") == "1.2.3"

    def test_plain_version(self):
        assert normalize_version("1.0.0") == "1.0.0"


class TestAnalysisEngine:
    def test_clean_release(self):
        result = AnalysisEngine.analyze(
            {
                "publisher": "TestCorp",
                "expected_publisher": "TestCorp",
                "version": "1.0.0",
                "source_type": "github",
                "raw_data": {"assets": []},
            }
        )
        assert result["signature_status"] == "valid"
        assert result["flag_status"] == FlagStatus.NONE
        assert result["confidence_score"] > 0

    def test_suspicious_assets(self):
        result = AnalysisEngine.analyze(
            {
                "publisher": "Unknown",
                "version": "1.0.0",
                "source_type": "github",
                "raw_data": {
                    "assets": [
                        {"name": "app-crack.exe"},
                        {"name": "keygen.zip"},
                    ]
                },
            }
        )
        assert len(result["suspicious_naming"]) > 0
        assert result["flag_status"] != FlagStatus.NONE


class TestCalculateOverallRisk:
    def test_blocked(self):
        assert calculate_overall_risk({"flag_status": "blocked"}) == "high"

    def test_none(self):
        assert calculate_overall_risk({"flag_status": "none"}) == "none"

    def test_enum_value(self):
        assert (
            calculate_overall_risk({"flag_status": FlagStatus.RESTRICTED}) == "medium"
        )
