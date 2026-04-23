"""Unit tests for IniSettingsManager.

All tests use a temporary directory so no real softarr.ini is touched.
"""

import configparser
import stat
import threading

import pytest

from softarr.core.ini_settings import SETTING_DEFINITIONS, IniSettingsManager


@pytest.fixture
def ini(tmp_path):
    """Create an IniSettingsManager backed by a temp file."""
    return IniSettingsManager(tmp_path / "softarr.ini")


# ------------------------------------------------------------------
# File creation and defaults
# ------------------------------------------------------------------


class TestFileCreation:
    def test_creates_default_file(self, tmp_path):
        path = tmp_path / "softarr.ini"
        assert not path.exists()
        IniSettingsManager(path)
        assert path.exists()

    def test_default_sections_present(self, ini, tmp_path):
        config = configparser.ConfigParser()
        config.read(tmp_path / "softarr.ini")
        assert config.has_section("misc")
        assert config.has_section("sabnzbd")
        assert config.has_section("adapters")
        assert config.get("misc", "version") == "1"

    def test_default_values_match_definitions(self, ini):
        for key, defn in SETTING_DEFINITIONS.items():
            assert ini.get(key) == defn["default"], f"Default mismatch for {key}"

    def test_file_permissions(self, tmp_path):
        path = tmp_path / "softarr.ini"
        IniSettingsManager(path)
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600

    def test_does_not_overwrite_existing(self, tmp_path):
        path = tmp_path / "softarr.ini"
        path.write_text("[misc]\nversion = 1\n\n[sabnzbd]\nurl = http://custom\n")
        ini = IniSettingsManager(path)
        assert ini.get("sabnzbd_url") == "http://custom"


# ------------------------------------------------------------------
# Get / Set
# ------------------------------------------------------------------


class TestGetSet:
    def test_get_unknown_key_returns_none(self, ini):
        assert ini.get("nonexistent_key") is None

    def test_set_and_get_round_trip(self, ini):
        ini.set("sabnzbd_url", "http://localhost:8080")
        assert ini.get("sabnzbd_url") == "http://localhost:8080"

    def test_set_overwrites_previous(self, ini):
        ini.set("sabnzbd_url", "http://first")
        ini.set("sabnzbd_url", "http://second")
        assert ini.get("sabnzbd_url") == "http://second"

    def test_set_unknown_key_raises(self, ini):
        with pytest.raises(ValueError, match="Unknown setting key"):
            ini.set("bogus_key", "value")

    def test_set_persists_to_disk(self, tmp_path):
        path = tmp_path / "softarr.ini"
        ini1 = IniSettingsManager(path)
        ini1.set("sabnzbd_url", "http://persisted")

        # New manager instance reads same file
        ini2 = IniSettingsManager(path)
        assert ini2.get("sabnzbd_url") == "http://persisted"


# ------------------------------------------------------------------
# Masking
# ------------------------------------------------------------------


class TestMasking:
    def test_secret_masked_in_get_all(self, ini):
        ini.set("sabnzbd_api_key", "my-secret-api-key-1234")
        masked = ini.get_all_masked()
        assert masked["sabnzbd_api_key"] == "****1234"
        assert masked["sabnzbd_api_key_is_set"] is True

    def test_short_secret_fully_masked(self, ini):
        ini.set("sabnzbd_api_key", "abc")
        masked = ini.get_all_masked()
        assert masked["sabnzbd_api_key"] == "****"

    def test_empty_secret_not_masked(self, ini):
        masked = ini.get_all_masked()
        assert masked["sabnzbd_api_key"] == ""
        assert masked["sabnzbd_api_key_is_set"] is False

    def test_non_secret_not_masked(self, ini):
        ini.set("sabnzbd_url", "http://example.com")
        masked = ini.get_all_masked()
        assert masked["sabnzbd_url"] == "http://example.com"

    def test_all_definitions_present(self, ini):
        masked = ini.get_all_masked()
        for key in SETTING_DEFINITIONS:
            assert key in masked
            assert f"{key}_is_set" in masked


# ------------------------------------------------------------------
# Indexer CRUD
# ------------------------------------------------------------------


class TestIndexerCRUD:
    def test_create_and_get(self, ini):
        result = ini.create_indexer(
            "NZB.su", "https://api.nzb.su", "key-123", enabled=True, priority=0
        )
        assert result["name"] == "NZB.su"
        assert result["url"] == "https://api.nzb.su"
        assert result["api_key"] == "key-123"
        assert result["enabled"] is True
        assert result["priority"] == 0

        fetched = ini.get_indexer("NZB.su")
        assert fetched is not None
        assert fetched["name"] == "NZB.su"
        assert fetched["api_key"] == "key-123"

    def test_create_duplicate_raises(self, ini):
        ini.create_indexer("Dupe", "http://a.test", "key-a")
        with pytest.raises(ValueError, match="already exists"):
            ini.create_indexer("Dupe", "http://b.test", "key-b")

    def test_get_nonexistent_returns_none(self, ini):
        assert ini.get_indexer("NoSuchIndexer") is None

    def test_get_indexers_empty(self, ini):
        assert ini.get_indexers() == []

    def test_get_indexers_sorted_by_priority(self, ini):
        ini.create_indexer("Beta", "http://b.test", "key-b", priority=5)
        ini.create_indexer("Alpha", "http://a.test", "key-a", priority=0)
        ini.create_indexer("Gamma", "http://g.test", "key-g", priority=5)

        indexers = ini.get_indexers()
        names = [i["name"] for i in indexers]
        # Alpha (p=0), then Beta and Gamma (p=5) sorted by name
        assert names == ["Alpha", "Beta", "Gamma"]

    def test_update_fields(self, ini):
        ini.create_indexer("Test", "http://old.test", "old-key", priority=0)
        updated = ini.update_indexer(
            "Test", url="http://new.test", api_key="new-key", priority=10
        )
        assert updated is not None
        assert updated["url"] == "http://new.test"
        assert updated["api_key"] == "new-key"
        assert updated["priority"] == 10

    def test_update_enabled(self, ini):
        ini.create_indexer("Toggle", "http://t.test", "key-t", enabled=True)
        updated = ini.update_indexer("Toggle", enabled=False)
        assert updated is not None
        assert updated["enabled"] is False

    def test_update_nonexistent_returns_none(self, ini):
        assert ini.update_indexer("Ghost", url="http://x.test") is None

    def test_update_rename(self, ini):
        ini.create_indexer("OldName", "http://o.test", "key-o")
        updated = ini.update_indexer("OldName", name="NewName")
        assert updated is not None
        assert updated["name"] == "NewName"
        assert ini.get_indexer("OldName") is None
        assert ini.get_indexer("NewName") is not None

    def test_rename_to_existing_raises(self, ini):
        ini.create_indexer("First", "http://1.test", "key-1")
        ini.create_indexer("Second", "http://2.test", "key-2")
        with pytest.raises(ValueError, match="already exists"):
            ini.update_indexer("First", name="Second")

    def test_delete(self, ini):
        ini.create_indexer("ToDelete", "http://d.test", "key-d")
        assert ini.delete_indexer("ToDelete") is True
        assert ini.get_indexer("ToDelete") is None

    def test_delete_nonexistent_returns_false(self, ini):
        assert ini.delete_indexer("NoSuch") is False

    def test_indexer_persists_to_disk(self, tmp_path):
        path = tmp_path / "softarr.ini"
        ini1 = IniSettingsManager(path)
        ini1.create_indexer("Persist", "http://p.test", "key-p")

        ini2 = IniSettingsManager(path)
        assert ini2.get_indexer("Persist") is not None


# ------------------------------------------------------------------
# Enabled indexer configs (for adapter use)
# ------------------------------------------------------------------


class TestEnabledConfigs:
    def test_returns_only_enabled(self, ini):
        ini.create_indexer("Active", "http://a.test", "key-a", enabled=True)
        ini.create_indexer("Inactive", "http://i.test", "key-i", enabled=False)

        configs = ini.get_enabled_indexer_configs()
        assert len(configs) == 1
        assert configs[0].name == "Active"

    def test_configs_are_unmasked(self, ini):
        ini.create_indexer("Plain", "http://p.test", "secret-key-12345", enabled=True)
        configs = ini.get_enabled_indexer_configs()
        assert configs[0].api_key == "secret-key-12345"

    def test_empty_when_no_indexers(self, ini):
        assert ini.get_enabled_indexer_configs() == []


# ------------------------------------------------------------------
# Thread safety smoke test
# ------------------------------------------------------------------


class TestThreadSafety:
    def test_concurrent_writes(self, ini):
        """Basic smoke test: many threads writing settings concurrently."""
        errors = []

        def writer(n):
            try:
                for i in range(20):
                    ini.set("sabnzbd_url", f"http://thread-{n}-iter-{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(n,)) for n in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        # Final value should be from one of the threads
        val = ini.get("sabnzbd_url")
        assert val is not None and val.startswith("http://thread-")


# ------------------------------------------------------------------
# TBI-13 -- Config export/import (via SettingsService)
# ------------------------------------------------------------------


class TestConfigExportImport:
    def test_export_returns_all_keys(self, ini):
        from softarr.services.settings_service import SettingsService

        svc = SettingsService(ini)
        result = svc.get_all_masked()
        # All defined keys should be present
        for key in SETTING_DEFINITIONS:
            assert key in result, f"Missing key: {key}"

    def test_export_masks_secrets(self, ini):
        from softarr.services.settings_service import SettingsService

        ini.set("sabnzbd_api_key", "mysecretkey")
        svc = SettingsService(ini)
        result = svc.get_all_masked()
        assert result["sabnzbd_api_key"] != "mysecretkey"
        assert "****" in result["sabnzbd_api_key"]

    def test_import_applies_valid_keys(self, ini):
        ini.set("sabnzbd_url", "http://old")
        incoming = {"sabnzbd_url": "http://new", "sabnzbd_category": "newsoftware"}
        applied = 0
        skipped = 0
        for key, value in incoming.items():
            if key not in SETTING_DEFINITIONS:
                skipped += 1
                continue
            if str(value).startswith("****"):
                skipped += 1
                continue
            ini.set(key, str(value))
            applied += 1
        assert applied == 2
        assert skipped == 0
        assert ini.get("sabnzbd_url") == "http://new"
        assert ini.get("sabnzbd_category") == "newsoftware"

    def test_import_skips_masked_values(self, ini):
        ini.set("sabnzbd_api_key", "realkey")
        incoming = {"sabnzbd_api_key": "****xxxx"}
        skipped = 0
        for key, value in incoming.items():
            if str(value).startswith("****"):
                skipped += 1
                continue
            ini.set(key, str(value))
        assert skipped == 1
        assert ini.get("sabnzbd_api_key") == "realkey"

    def test_import_skips_unknown_keys(self, ini):
        incoming = {"not_a_real_key": "somevalue", "sabnzbd_url": "http://valid"}
        applied = 0
        skipped = 0
        for key, value in incoming.items():
            if key not in SETTING_DEFINITIONS:
                skipped += 1
                continue
            ini.set(key, str(value))
            applied += 1
        assert applied == 1
        assert skipped == 1


# ------------------------------------------------------------------
# Phase 4 / Phase 5 -- new SETTING_DEFINITIONS entries
# ------------------------------------------------------------------


class TestNewSettingDefinitions:
    def test_torznab_adapter_enabled_defined(self, ini):
        """torznab_adapter_enabled must be in SETTING_DEFINITIONS."""
        assert "torznab_adapter_enabled" in SETTING_DEFINITIONS

    def test_torznab_adapter_enabled_default_false(self, ini):
        assert ini.get("torznab_adapter_enabled") == "false"

    def test_hash_verification_enabled_defined(self, ini):
        assert "hash_verification_enabled" in SETTING_DEFINITIONS

    def test_hash_verification_enabled_default_true(self, ini):
        assert ini.get("hash_verification_enabled") == "true"

    def test_totp_required_defined(self, ini):
        assert "totp_required" in SETTING_DEFINITIONS

    def test_totp_required_default_false(self, ini):
        assert ini.get("totp_required") == "false"

    def test_nsrl_enabled_default_is_true(self, ini):
        """nsrl_enabled default changed from false to true in Phase 4."""
        assert ini.get("nsrl_enabled") == "true"

    def test_antipiracy_enabled_default_is_true(self, ini):
        """antipiracy_enabled default changed from false to true in Phase 4."""
        assert ini.get("antipiracy_enabled") == "true"

    def test_new_settings_can_be_set(self, ini):
        ini.set("torznab_adapter_enabled", "true")
        assert ini.get("torznab_adapter_enabled") == "true"

        ini.set("hash_verification_enabled", "false")
        assert ini.get("hash_verification_enabled") == "false"

        ini.set("totp_required", "true")
        assert ini.get("totp_required") == "true"

    def test_quick_approve_mode_default_false(self, ini):
        """quick_approve_mode_enabled is disabled by default."""
        assert ini.get("quick_approve_mode_enabled") == "false"

    def test_ai_enabled_default_false(self, ini):
        """AI assistant is disabled by default."""
        assert ini.get("ai_enabled") == "false"

    def test_ai_settings_defaults(self, ini):
        """AI settings have expected defaults."""
        assert ini.get("ai_provider") == "openai"
        assert ini.get("ai_model") == "gpt-4o-mini"
        assert ini.get("ai_rate_limit_per_hour") == "20"

    def test_staging_auto_cleanup_default_zero(self, ini):
        """Staging auto-cleanup is disabled by default (0 days)."""
        assert ini.get("staging_auto_cleanup_days") == "0"

    def test_push_notifications_default_false(self, ini):
        """Web Push notifications are disabled by default."""
        assert ini.get("push_notifications_enabled") == "false"

    def test_default_search_mode_is_standard(self, ini):
        """Default search mode is 'standard'."""
        assert ini.get("default_search_mode") == "standard"

    def test_quick_approve_mode_can_be_toggled(self, ini):
        """quick_approve_mode_enabled can be set via the settings manager."""
        ini.set("quick_approve_mode_enabled", "true")
        assert ini.get("quick_approve_mode_enabled") == "true"
