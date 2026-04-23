"""Unit tests for UsenetIndexerService and API-key masking.

All tests use a temporary INI file via the tmp_path fixture.
"""

import pytest

from softarr.core.ini_settings import IniSettingsManager
from softarr.schemas.usenet_indexer import UsenetIndexerCreate, UsenetIndexerUpdate
from softarr.services.usenet_indexer_service import UsenetIndexerService


@pytest.fixture
def ini(tmp_path):
    return IniSettingsManager(tmp_path / "softarr.ini")


class TestMaskApiKey:
    def test_long_key(self):
        assert UsenetIndexerService._mask_api_key("abcdefghij") == "****ghij"

    def test_five_char_key(self):
        assert UsenetIndexerService._mask_api_key("abcde") == "****bcde"

    def test_four_char_key(self):
        assert UsenetIndexerService._mask_api_key("abcd") == "****"

    def test_short_key(self):
        assert UsenetIndexerService._mask_api_key("ab") == "****"

    def test_empty_key(self):
        assert UsenetIndexerService._mask_api_key("") == ""


class TestUsenetIndexerCRUD:
    def test_create_indexer(self, ini):
        service = UsenetIndexerService(ini)
        result = service.create(
            UsenetIndexerCreate(
                name="NZB.su",
                url="https://api.nzb.su",
                api_key="super-secret-key-12345",
            )
        )
        assert result.name == "NZB.su"
        assert result.url == "https://api.nzb.su"
        assert result.api_key == "****2345"  # Masked
        assert result.enabled is True
        assert result.priority == 0

    def test_create_with_custom_priority(self, ini):
        service = UsenetIndexerService(ini)
        result = service.create(
            UsenetIndexerCreate(
                name="DogNZB",
                url="https://api.dognzb.cr",
                api_key="key123",
                enabled=False,
                priority=5,
            )
        )
        assert result.enabled is False
        assert result.priority == 5

    def test_list_ordered_by_priority(self, ini):
        service = UsenetIndexerService(ini)
        service.create(
            UsenetIndexerCreate(
                name="Low", url="http://low", api_key="k1111", priority=10
            )
        )
        service.create(
            UsenetIndexerCreate(
                name="High", url="http://high", api_key="k2222", priority=0
            )
        )
        service.create(
            UsenetIndexerCreate(
                name="Mid", url="http://mid", api_key="k3333", priority=5
            )
        )

        results = service.get_all()
        assert [r.name for r in results] == ["High", "Mid", "Low"]

    def test_get_by_name(self, ini):
        service = UsenetIndexerService(ini)
        service.create(
            UsenetIndexerCreate(name="Test", url="http://test", api_key="key12345")
        )
        fetched = service.get_by_name("Test")
        assert fetched is not None
        assert fetched.name == "Test"

    def test_get_nonexistent_returns_none(self, ini):
        service = UsenetIndexerService(ini)
        assert service.get_by_name("NoSuch") is None

    def test_partial_update(self, ini):
        service = UsenetIndexerService(ini)
        service.create(
            UsenetIndexerCreate(
                name="Original", url="http://orig", api_key="origkey123"
            )
        )
        updated = service.update("Original", UsenetIndexerUpdate(url="http://new"))
        assert updated is not None
        assert updated.name == "Original"
        assert updated.url == "http://new"
        assert updated.api_key == "****y123"  # Still masked original key

    def test_update_api_key(self, ini):
        service = UsenetIndexerService(ini)
        service.create(
            UsenetIndexerCreate(name="Test", url="http://t", api_key="oldkey12345")
        )
        updated = service.update("Test", UsenetIndexerUpdate(api_key="newkey99999"))
        assert updated is not None
        assert updated.api_key == "****9999"  # New masked key

    def test_update_nonexistent_returns_none(self, ini):
        service = UsenetIndexerService(ini)
        result = service.update("Ghost", UsenetIndexerUpdate(url="http://x"))
        assert result is None

    def test_delete_indexer(self, ini):
        service = UsenetIndexerService(ini)
        service.create(
            UsenetIndexerCreate(name="Del", url="http://del", api_key="delkey12345")
        )
        assert service.delete("Del") is True
        assert service.get_by_name("Del") is None

    def test_delete_nonexistent_returns_false(self, ini):
        service = UsenetIndexerService(ini)
        assert service.delete("NoSuch") is False


class TestGetAllEnabledConfigs:
    def test_only_enabled_returned(self, ini):
        service = UsenetIndexerService(ini)
        service.create(
            UsenetIndexerCreate(
                name="Enabled", url="http://on", api_key="key11111", enabled=True
            )
        )
        service.create(
            UsenetIndexerCreate(
                name="Disabled", url="http://off", api_key="key22222", enabled=False
            )
        )

        configs = service.get_all_enabled_configs()
        assert len(configs) == 1
        assert configs[0].name == "Enabled"

    def test_configs_are_unmasked(self, ini):
        service = UsenetIndexerService(ini)
        service.create(
            UsenetIndexerCreate(
                name="Test", url="http://t", api_key="raw-secret-key-99"
            )
        )

        configs = service.get_all_enabled_configs()
        assert len(configs) == 1
        assert configs[0].api_key == "raw-secret-key-99"  # Not masked

    def test_ordered_by_priority(self, ini):
        service = UsenetIndexerService(ini)
        service.create(
            UsenetIndexerCreate(
                name="Second", url="http://b", api_key="k2222", priority=5
            )
        )
        service.create(
            UsenetIndexerCreate(
                name="First", url="http://a", api_key="k1111", priority=1
            )
        )

        configs = service.get_all_enabled_configs()
        assert [c.name for c in configs] == ["First", "Second"]

    def test_empty_when_none_configured(self, ini):
        service = UsenetIndexerService(ini)
        configs = service.get_all_enabled_configs()
        assert configs == []


class TestCategories:
    """categories field is stored, retrieved, and passed to UsenetIndexerConfig."""

    DEFAULT_CATS = "4000,4010,4020,4030,4040,4050,4060,4070"

    def test_default_categories_on_create(self, ini):
        service = UsenetIndexerService(ini)
        result = service.create(
            UsenetIndexerCreate(name="Test", url="http://t", api_key="key12345")
        )
        assert result.categories == self.DEFAULT_CATS

    def test_custom_categories_persisted(self, ini):
        service = UsenetIndexerService(ini)
        custom = "5000,5010,5020"
        service.create(
            UsenetIndexerCreate(
                name="Custom",
                url="http://custom",
                api_key="key12345",
                categories=custom,
            )
        )
        fetched = service.get_by_name("Custom")
        assert fetched is not None
        assert fetched.categories == custom

    def test_categories_update(self, ini):
        service = UsenetIndexerService(ini)
        service.create(
            UsenetIndexerCreate(name="Upd", url="http://upd", api_key="key12345")
        )
        updated = service.update("Upd", UsenetIndexerUpdate(categories="1000,2000"))
        assert updated is not None
        assert updated.categories == "1000,2000"

    def test_categories_in_enabled_config(self, ini):
        service = UsenetIndexerService(ini)
        service.create(
            UsenetIndexerCreate(
                name="Cfg",
                url="http://cfg",
                api_key="key12345",
                categories="9000,9010",
            )
        )
        configs = service.get_all_enabled_configs()
        assert len(configs) == 1
        assert configs[0].categories == "9000,9010"
