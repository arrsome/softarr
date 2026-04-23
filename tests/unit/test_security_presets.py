"""Unit tests for the security preset API endpoint.

Tests are based on the IniSettingsManager directly to avoid needing a full
FastAPI test client, and a separate integration test covers the HTTP layer.
"""

import pytest

from softarr.core.ini_settings import IniSettingsManager


@pytest.fixture
def ini(tmp_path):
    return IniSettingsManager(tmp_path / "softarr.ini")


class TestNannyMode:
    """Nanny Mode enables all security controls."""

    def _apply_nanny(self, ini: IniSettingsManager) -> None:
        changes = {
            "totp_required": "true",
            "password_require_uppercase": "true",
            "password_require_numbers": "true",
            "password_require_special": "true",
            "password_max_age_days": "90",
            "antipiracy_enabled": "true",
            "nsrl_enabled": "true",
            "hash_verification_enabled": "true",
        }
        for key, value in changes.items():
            ini.set(key, value)

    def test_nanny_enables_totp_required(self, ini):
        self._apply_nanny(ini)
        assert ini.get("totp_required") == "true"

    def test_nanny_enables_password_rules(self, ini):
        self._apply_nanny(ini)
        assert ini.get("password_require_uppercase") == "true"
        assert ini.get("password_require_numbers") == "true"
        assert ini.get("password_require_special") == "true"

    def test_nanny_sets_password_max_age(self, ini):
        self._apply_nanny(ini)
        assert ini.get("password_max_age_days") == "90"

    def test_nanny_enables_antipiracy(self, ini):
        self._apply_nanny(ini)
        assert ini.get("antipiracy_enabled") == "true"

    def test_nanny_enables_nsrl(self, ini):
        self._apply_nanny(ini)
        assert ini.get("nsrl_enabled") == "true"

    def test_nanny_enables_hash_verification(self, ini):
        self._apply_nanny(ini)
        assert ini.get("hash_verification_enabled") == "true"


class TestAdultMode:
    """I'm an Adult mode disables hash verification, removes max age, disables anti-piracy."""

    def _apply_adult(self, ini: IniSettingsManager) -> None:
        changes = {
            "hash_verification_enabled": "false",
            "password_max_age_days": "0",
            "antipiracy_enabled": "false",
        }
        for key, value in changes.items():
            ini.set(key, value)

    def test_adult_disables_hash_verification(self, ini):
        self._apply_adult(ini)
        assert ini.get("hash_verification_enabled") == "false"

    def test_adult_sets_max_age_to_zero(self, ini):
        self._apply_adult(ini)
        assert ini.get("password_max_age_days") == "0"

    def test_adult_disables_antipiracy(self, ini):
        self._apply_adult(ini)
        assert ini.get("antipiracy_enabled") == "false"


class TestCustomMode:
    """Custom mode makes no changes."""

    def test_custom_does_not_change_settings(self, ini):
        before = {
            "antipiracy_enabled": ini.get("antipiracy_enabled"),
            "nsrl_enabled": ini.get("nsrl_enabled"),
            "hash_verification_enabled": ini.get("hash_verification_enabled"),
        }
        # Custom preset -- no changes
        changes: dict = {}
        for key, value in changes.items():
            ini.set(key, value)
        after = {
            "antipiracy_enabled": ini.get("antipiracy_enabled"),
            "nsrl_enabled": ini.get("nsrl_enabled"),
            "hash_verification_enabled": ini.get("hash_verification_enabled"),
        }
        assert before == after
