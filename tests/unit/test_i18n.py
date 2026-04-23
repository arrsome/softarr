"""Unit tests for the i18n translation module (src/softarr/core/i18n.py)."""

import json
import logging

import pytest

from softarr.core.i18n import SUPPORTED_LANGUAGES, reload_locales, t


@pytest.fixture(autouse=True)
def clear_cache():
    """Clear the locale cache before and after every test."""
    reload_locales()
    yield
    reload_locales()


class TestTranslationFunction:
    def test_english_key_returns_correct_value(self):
        """t() with lang='en' returns the English string."""
        result = t("nav.dashboard", "en")
        assert result == "Dashboard"

    def test_default_lang_is_english(self):
        """t() without lang argument defaults to English."""
        result = t("nav.dashboard")
        assert result == "Dashboard"

    def test_missing_key_returns_key_itself(self):
        """t() returns the key string when the key is absent in all locales."""
        result = t("this.key.does.not.exist", "en")
        assert result == "this.key.does.not.exist"

    def test_missing_key_logs_warning(self, caplog):
        """t() logs a WARNING when a key is missing."""
        with caplog.at_level(logging.WARNING, logger="softarr.i18n"):
            t("missing.key.xyz", "en")
        assert any("missing.key.xyz" in record.message for record in caplog.records)

    def test_fallback_to_english_for_non_english_locale(self, tmp_path, monkeypatch):
        """When a key is in English but not in the requested locale, fallback occurs."""
        import softarr.core.i18n as i18n_module

        # Create a German locale file that is missing a key present in English
        locales_dir = tmp_path / "locales"
        locales_dir.mkdir()
        (locales_dir / "en.json").write_text(
            json.dumps({"nav.dashboard": "Dashboard", "btn.save": "Save"}),
            encoding="utf-8",
        )
        (locales_dir / "de.json").write_text(
            json.dumps({"nav.dashboard": "Startseite"}),  # btn.save missing
            encoding="utf-8",
        )

        monkeypatch.setattr(i18n_module, "_LOCALES_DIR", locales_dir)
        reload_locales()

        # Key present in German locale -- use German
        assert t("nav.dashboard", "de") == "Startseite"
        # Key absent in German -- fall back to English
        assert t("btn.save", "de") == "Save"

    def test_missing_in_both_locales_returns_key(self, tmp_path, monkeypatch):
        """If the key is missing in both requested and English locales, return the key."""
        import softarr.core.i18n as i18n_module

        locales_dir = tmp_path / "locales"
        locales_dir.mkdir()
        (locales_dir / "en.json").write_text(json.dumps({}), encoding="utf-8")
        (locales_dir / "fr.json").write_text(json.dumps({}), encoding="utf-8")

        monkeypatch.setattr(i18n_module, "_LOCALES_DIR", locales_dir)
        reload_locales()

        result = t("some.absent.key", "fr")
        assert result == "some.absent.key"

    def test_locale_file_not_found_returns_key(self, tmp_path, monkeypatch):
        """If the locale file does not exist, t() returns the key."""
        import softarr.core.i18n as i18n_module

        locales_dir = tmp_path / "locales"
        locales_dir.mkdir()
        (locales_dir / "en.json").write_text(json.dumps({}), encoding="utf-8")
        # xx.json intentionally not created

        monkeypatch.setattr(i18n_module, "_LOCALES_DIR", locales_dir)
        reload_locales()

        result = t("nav.dashboard", "xx")
        assert result == "nav.dashboard"


class TestSupportedLanguages:
    def test_supported_languages_is_frozenset(self):
        assert isinstance(SUPPORTED_LANGUAGES, frozenset)

    def test_english_is_supported(self):
        assert "en" in SUPPORTED_LANGUAGES

    def test_all_ten_languages_present(self):
        expected = {"en", "de", "es", "fr", "it", "ja", "ko", "pt", "zh", "ar"}
        assert expected == SUPPORTED_LANGUAGES


class TestReloadLocales:
    def test_reload_clears_cache(self, tmp_path, monkeypatch):
        """reload_locales() causes the next t() call to re-read from disk."""
        import softarr.core.i18n as i18n_module

        locales_dir = tmp_path / "locales"
        locales_dir.mkdir()
        en_path = locales_dir / "en.json"
        en_path.write_text(json.dumps({"test.key": "first"}), encoding="utf-8")

        monkeypatch.setattr(i18n_module, "_LOCALES_DIR", locales_dir)
        reload_locales()

        assert t("test.key", "en") == "first"

        # Update the file on disk
        en_path.write_text(json.dumps({"test.key": "second"}), encoding="utf-8")

        # Before reload, still returns cached value
        assert t("test.key", "en") == "first"

        # After reload, returns new value
        reload_locales()
        assert t("test.key", "en") == "second"
