"""INI file-backed settings service.

Thin wrapper around IniSettingsManager that provides the same interface
previously offered by the DB-backed SettingsService. All methods are
synchronous since INI file operations do not require async I/O.
"""

from typing import Any, Dict, Optional

from softarr.core.ini_settings import SETTING_DEFINITIONS, IniSettingsManager


class SettingsService:
    def __init__(self, ini: IniSettingsManager):
        self.ini = ini

    def get(self, key: str) -> Optional[str]:
        """Get a setting value by key. Returns None if not set."""
        return self.ini.get(key)

    def set(self, key: str, value: str) -> None:
        """Set a setting value."""
        self.ini.set(key, value)

    def get_all_masked(self) -> Dict[str, Any]:
        """Get all settings with secrets masked for API/UI display."""
        return self.ini.get_all_masked()


__all__ = ["SETTING_DEFINITIONS", "SettingsService"]
