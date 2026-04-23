"""Tests for release rules: version pinning, auto-reject, and release type filter."""

from softarr.services.release_rules_service import (
    check_auto_reject_rules,
    check_release_type_filter,
    check_version_pin,
)


class TestVersionPin:
    def test_no_pin_allows_everything(self):
        allowed, reason = check_version_pin("2.1.0", None)
        assert allowed is True
        assert reason == ""

    def test_disabled_mode_allows_everything(self):
        allowed, _ = check_version_pin("2.1.0", {"mode": "disabled"})
        assert allowed is True

    def test_exact_match_allows(self):
        allowed, _ = check_version_pin("1.2.3", {"mode": "exact", "value": "1.2.3"})
        assert allowed is True

    def test_exact_mismatch_rejects(self):
        allowed, reason = check_version_pin(
            "1.3.0", {"mode": "exact", "value": "1.2.3"}
        )
        assert allowed is False
        assert "1.2.3" in reason

    def test_major_match_allows(self):
        allowed, _ = check_version_pin("1.9.5", {"mode": "major", "value": "1"})
        assert allowed is True

    def test_major_mismatch_rejects(self):
        allowed, reason = check_version_pin("2.0.0", {"mode": "major", "value": "1"})
        assert allowed is False
        assert "1.x.x" in reason

    def test_empty_pin_value_allows(self):
        allowed, _ = check_version_pin("3.0.0", {"mode": "exact", "value": ""})
        assert allowed is True


class TestAutoRejectRules:
    def test_no_rules_never_rejects(self):
        reject, reason = check_auto_reject_rules("1.0.0-beta1", "MyApp", [], [])
        assert reject is False

    def test_pre_release_rule_catches_beta(self):
        reject, reason = check_auto_reject_rules(
            "1.0.0-beta1", "MyApp", [], ["pre_release"]
        )
        assert reject is True
        assert "pre_release" in reason

    def test_pre_release_rule_catches_alpha(self):
        reject, _ = check_auto_reject_rules("2.0.0-alpha", "App", [], ["pre_release"])
        assert reject is True

    def test_pre_release_rule_catches_rc(self):
        reject, _ = check_auto_reject_rules("3.0-rc1", "App", [], ["pre_release"])
        assert reject is True

    def test_pre_release_does_not_catch_stable(self):
        reject, _ = check_auto_reject_rules("1.0.0", "MyApp", [], ["pre_release"])
        assert reject is False

    def test_nightly_rule_catches_nightly(self):
        reject, _ = check_auto_reject_rules("20250101-nightly", "App", [], ["nightly"])
        assert reject is True

    def test_nightly_rule_catches_dev(self):
        reject, _ = check_auto_reject_rules("1.0.0-dev", "App", [], ["nightly"])
        assert reject is True

    def test_portable_rule(self):
        reject, _ = check_auto_reject_rules("1.0", "MyApp-portable", [], ["portable"])
        assert reject is True

    def test_portable_not_triggered_without_rule(self):
        reject, _ = check_auto_reject_rules("1.0", "MyApp-portable", [], [])
        assert reject is False

    def test_wrong_publisher_rejected(self):
        reject, reason = check_auto_reject_rules(
            "1.0",
            "App",
            [],
            ["wrong_publisher"],
            publisher="EvilCorp",
            expected_publisher="TrustedVendor",
        )
        assert reject is True
        assert "EvilCorp" in reason

    def test_matching_publisher_not_rejected(self):
        reject, _ = check_auto_reject_rules(
            "1.0",
            "App",
            [],
            ["wrong_publisher"],
            publisher="TrustedVendor",
            expected_publisher="TrustedVendor",
        )
        assert reject is False

    def test_unsigned_rule_triggers_on_invalid_signature(self):
        reject, _ = check_auto_reject_rules(
            "1.0", "App", [], ["unsigned"], signature_status="invalid"
        )
        assert reject is True

    def test_unsigned_rule_ignores_valid_signature(self):
        reject, _ = check_auto_reject_rules(
            "1.0", "App", [], ["unsigned"], signature_status="valid"
        )
        assert reject is False


class TestReleaseTypeFilter:
    def test_empty_filter_allows_all(self):
        allowed, _ = check_release_type_filter("1.0", "App", [], [])
        assert allowed is True

    def test_installer_detected_from_name(self):
        allowed, _ = check_release_type_filter(
            "1.0", "App-Setup.exe", [], ["installer"]
        )
        assert allowed is True

    def test_installer_blocked_when_only_archive_allowed(self):
        allowed, reason = check_release_type_filter(
            "1.0", "App-Setup.exe", [], ["archive"]
        )
        assert allowed is False
        assert "installer" in reason

    def test_archive_detected(self):
        allowed, _ = check_release_type_filter(
            "1.0", "App", ["softarr.tar.gz"], ["archive"]
        )
        assert allowed is True

    def test_source_detected(self):
        allowed, _ = check_release_type_filter("1.0", "App-source", [], ["source"])
        assert allowed is True

    def test_multiple_allowed_types(self):
        allowed, _ = check_release_type_filter(
            "1.0", "App.zip", [], ["installer", "archive"]
        )
        assert allowed is True
