"""INI file-backed settings manager.

Replaces the database-backed AppSetting and UsenetIndexer tables with a
human-readable softarr.ini file. Settings are grouped into sections:

  [misc]       Config file version
  [sabnzbd]    SABnzbd connection settings
  [adapters]   Source adapter toggles
  [indexer:*]  One section per Newznab-compatible indexer

Thread safety is provided by a threading.Lock around all read/write cycles.
Writes are atomic (write to temp file, then os.replace).
"""

import configparser
import os
import stat
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from softarr.adapters.usenet import UsenetIndexerConfig

# ---------------------------------------------------------------------------
# Setting definitions -- key -> (section, ini_key, default, is_secret)
# ---------------------------------------------------------------------------

SETTING_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "sabnzbd_url": {
        "section": "sabnzbd",
        "ini_key": "url",
        "default": "",
        "is_secret": False,
    },
    "sabnzbd_api_key": {
        "section": "sabnzbd",
        "ini_key": "api_key",
        "default": "",
        "is_secret": True,
    },
    "sabnzbd_category": {
        "section": "sabnzbd",
        "ini_key": "category",
        "default": "software",
        "is_secret": False,
    },
    "sabnzbd_ssl_verify": {
        "section": "sabnzbd",
        "ini_key": "ssl_verify",
        "default": "true",
        "is_secret": False,
    },
    "sabnzbd_timeout": {
        "section": "sabnzbd",
        "ini_key": "timeout",
        "default": "30",
        "is_secret": False,
    },
    "github_adapter_enabled": {
        "section": "adapters",
        "ini_key": "github_enabled",
        "default": "true",
        "is_secret": False,
    },
    "usenet_adapter_enabled": {
        "section": "adapters",
        "ini_key": "usenet_enabled",
        "default": "false",
        "is_secret": False,
    },
    "github_token": {
        "section": "adapters",
        "ini_key": "github_token",
        "default": "",
        "is_secret": True,
    },
    "github_repo": {
        "section": "adapters",
        "ini_key": "github_repo",
        "default": "arrsome/softarr",
        "is_secret": False,
    },
    "virustotal_enabled": {
        "section": "hash_sources",
        "ini_key": "virustotal_enabled",
        "default": "false",
        "is_secret": False,
    },
    "virustotal_api_key": {
        "section": "hash_sources",
        "ini_key": "virustotal_api_key",
        "default": "",
        "is_secret": True,
    },
    "nsrl_enabled": {
        "section": "hash_sources",
        "ini_key": "nsrl_enabled",
        "default": "true",
        "is_secret": False,
    },
    # --- Retention policy ---
    "retention_enabled": {
        "section": "retention",
        "ini_key": "enabled",
        "default": "false",
        "is_secret": False,
    },
    "retention_discovered_days": {
        "section": "retention",
        "ini_key": "discovered_days",
        "default": "30",
        "is_secret": False,
    },
    "retention_rejected_enabled": {
        "section": "retention",
        "ini_key": "rejected_enabled",
        "default": "false",
        "is_secret": False,
    },
    "retention_rejected_days": {
        "section": "retention",
        "ini_key": "rejected_days",
        "default": "90",
        "is_secret": False,
    },
    "retention_keep_downloaded_count": {
        "section": "retention",
        "ini_key": "keep_downloaded_count",
        "default": "0",
        "is_secret": False,
    },
    "audit_retention_days": {
        "section": "retention",
        "ini_key": "audit_retention_days",
        "default": "365",
        "is_secret": False,
    },
    # --- Scheduler ---
    "scheduler_enabled": {
        "section": "scheduler",
        "ini_key": "enabled",
        "default": "false",
        "is_secret": False,
    },
    "scheduler_interval_minutes": {
        "section": "scheduler",
        "ini_key": "interval_minutes",
        "default": "60",
        "is_secret": False,
    },
    "auto_queue_upgrades": {
        "section": "scheduler",
        "ini_key": "auto_queue_upgrades",
        "default": "false",
        "is_secret": False,
    },
    # --- Backup ---
    "backup_enabled": {
        "section": "backup",
        "ini_key": "enabled",
        "default": "false",
        "is_secret": False,
    },
    "backup_dir": {
        "section": "backup",
        "ini_key": "dir",
        "default": "",
        "is_secret": False,
    },
    "backup_interval_hours": {
        "section": "backup",
        "ini_key": "interval_hours",
        "default": "24",
        "is_secret": False,
    },
    "backup_keep_count": {
        "section": "backup",
        "ini_key": "keep_count",
        "default": "7",
        "is_secret": False,
    },
    # --- Security / password policy ---
    "password_min_length": {
        "section": "security",
        "ini_key": "password_min_length",
        "default": "12",
        "is_secret": False,
    },
    "password_require_uppercase": {
        "section": "security",
        "ini_key": "password_require_uppercase",
        "default": "false",
        "is_secret": False,
    },
    "password_require_numbers": {
        "section": "security",
        "ini_key": "password_require_numbers",
        "default": "false",
        "is_secret": False,
    },
    "password_require_special": {
        "section": "security",
        "ini_key": "password_require_special",
        "default": "false",
        "is_secret": False,
    },
    "password_history_count": {
        "section": "security",
        "ini_key": "password_history_count",
        "default": "5",
        "is_secret": False,
    },
    "password_max_age_days": {
        "section": "security",
        "ini_key": "password_max_age_days",
        "default": "0",
        "is_secret": False,
    },
    # --- Notifications ---
    "notifications_enabled": {
        "section": "notifications",
        "ini_key": "enabled",
        "default": "false",
        "is_secret": False,
    },
    "notify_on_new_release": {
        "section": "notifications",
        "ini_key": "notify_on_new_release",
        "default": "true",
        "is_secret": False,
    },
    "notify_on_flagged": {
        "section": "notifications",
        "ini_key": "notify_on_flagged",
        "default": "true",
        "is_secret": False,
    },
    "notify_on_download": {
        "section": "notifications",
        "ini_key": "notify_on_download",
        "default": "false",
        "is_secret": False,
    },
    "email_enabled": {
        "section": "notifications",
        "ini_key": "email_enabled",
        "default": "false",
        "is_secret": False,
    },
    "email_smtp_host": {
        "section": "notifications",
        "ini_key": "email_smtp_host",
        "default": "",
        "is_secret": False,
    },
    "email_smtp_port": {
        "section": "notifications",
        "ini_key": "email_smtp_port",
        "default": "587",
        "is_secret": False,
    },
    "email_smtp_user": {
        "section": "notifications",
        "ini_key": "email_smtp_user",
        "default": "",
        "is_secret": False,
    },
    "email_smtp_password": {
        "section": "notifications",
        "ini_key": "email_smtp_password",
        "default": "",
        "is_secret": True,
    },
    "email_from": {
        "section": "notifications",
        "ini_key": "email_from",
        "default": "",
        "is_secret": False,
    },
    "email_to": {
        "section": "notifications",
        "ini_key": "email_to",
        "default": "",
        "is_secret": False,
    },
    "discord_webhook_enabled": {
        "section": "notifications",
        "ini_key": "discord_webhook_enabled",
        "default": "false",
        "is_secret": False,
    },
    "discord_webhook_url": {
        "section": "notifications",
        "ini_key": "discord_webhook_url",
        "default": "",
        "is_secret": True,
    },
    "http_webhook_enabled": {
        "section": "notifications",
        "ini_key": "http_webhook_enabled",
        "default": "false",
        "is_secret": False,
    },
    "http_webhook_url": {
        "section": "notifications",
        "ini_key": "http_webhook_url",
        "default": "",
        "is_secret": False,
    },
    # --- Expanded hash intelligence ---
    "circl_hashlookup_enabled": {
        "section": "hash_sources",
        "ini_key": "circl_hashlookup_enabled",
        "default": "false",
        "is_secret": False,
    },
    "malwarebazaar_enabled": {
        "section": "hash_sources",
        "ini_key": "malwarebazaar_enabled",
        "default": "false",
        "is_secret": False,
    },
    "misp_warninglists_enabled": {
        "section": "hash_sources",
        "ini_key": "misp_warninglists_enabled",
        "default": "false",
        "is_secret": False,
    },
    "hash_recheck_interval_hours": {
        "section": "hash_sources",
        "ini_key": "hash_recheck_interval_hours",
        "default": "24",
        "is_secret": False,
    },
    # --- API key (machine-to-machine, X-Api-Key header) ---
    "api_key": {
        "section": "security",
        "ini_key": "api_key",
        "default": "",
        "is_secret": True,
    },
    # --- Apprise webhook notification ---
    "apprise_webhook_enabled": {
        "section": "notifications",
        "ini_key": "apprise_webhook_enabled",
        "default": "false",
        "is_secret": False,
    },
    "apprise_webhook_url": {
        "section": "notifications",
        "ini_key": "apprise_webhook_url",
        "default": "",
        "is_secret": True,
    },
    "notify_on_upgrade": {
        "section": "notifications",
        "ini_key": "notify_on_upgrade",
        "default": "true",
        "is_secret": False,
    },
    "notify_on_download_complete": {
        "section": "notifications",
        "ini_key": "notify_on_download_complete",
        "default": "true",
        "is_secret": False,
    },
    # --- SABnzbd webhook secret (for inbound hooks) ---
    "sabnzbd_webhook_secret": {
        "section": "sabnzbd",
        "ini_key": "webhook_secret",
        "default": "",
        "is_secret": True,
    },
    # --- qBittorrent download client ---
    "qbittorrent_url": {
        "section": "qbittorrent",
        "ini_key": "url",
        "default": "",
        "is_secret": False,
    },
    "qbittorrent_username": {
        "section": "qbittorrent",
        "ini_key": "username",
        "default": "",
        "is_secret": False,
    },
    "qbittorrent_password": {
        "section": "qbittorrent",
        "ini_key": "password",
        "default": "",
        "is_secret": True,
    },
    "qbittorrent_category": {
        "section": "qbittorrent",
        "ini_key": "category",
        "default": "software",
        "is_secret": False,
    },
    "qbittorrent_ssl_verify": {
        "section": "qbittorrent",
        "ini_key": "ssl_verify",
        "default": "true",
        "is_secret": False,
    },
    "qbittorrent_timeout": {
        "section": "qbittorrent",
        "ini_key": "timeout",
        "default": "30",
        "is_secret": False,
    },
    "qbittorrent_webhook_secret": {
        "section": "qbittorrent",
        "ini_key": "webhook_secret",
        "default": "",
        "is_secret": True,
    },
    # --- Active download client selector ---
    "active_download_client": {
        "section": "download_clients",
        "ini_key": "active_client",
        "default": "sabnzbd",
        "is_secret": False,
    },
    "qbittorrent_poll_interval_seconds": {
        "section": "download_clients",
        "ini_key": "qbittorrent_poll_interval_seconds",
        "default": "60",
        "is_secret": False,
    },
    # --- Per-channel notification event filters (TBI-15) ---
    # Values: "all" (default) or comma-separated event names, e.g.
    # "new_release_discovered,release_flagged"
    "discord_events": {
        "section": "notifications",
        "ini_key": "discord_events",
        "default": "all",
        "is_secret": False,
    },
    "http_webhook_events": {
        "section": "notifications",
        "ini_key": "http_webhook_events",
        "default": "all",
        "is_secret": False,
    },
    "apprise_events": {
        "section": "notifications",
        "ini_key": "apprise_events",
        "default": "all",
        "is_secret": False,
    },
    "email_events": {
        "section": "notifications",
        "ini_key": "email_events",
        "default": "all",
        "is_secret": False,
    },
    # --- 2FA / TOTP ---
    "totp_issuer": {
        "section": "security",
        "ini_key": "totp_issuer",
        "default": "Softarr",
        "is_secret": False,
    },
    # --- Heuristic sensitivity (TBI-ADV-INT-01) ---
    # Controls how aggressively the analysis flags releases.
    # Values: "low" | "medium" | "high"
    "heuristic_sensitivity": {
        "section": "analysis",
        "ini_key": "heuristic_sensitivity",
        "default": "medium",
        "is_secret": False,
    },
    # --- Anti-piracy content filter ---
    "antipiracy_enabled": {
        "section": "security",
        "ini_key": "antipiracy_enabled",
        "default": "true",
        "is_secret": False,
    },
    # --- Torznab / torrent adapter ---
    "torznab_adapter_enabled": {
        "section": "adapters",
        "ini_key": "torznab_enabled",
        "default": "false",
        "is_secret": False,
    },
    # --- Hash verification master toggle ---
    "hash_verification_enabled": {
        "section": "hash_sources",
        "ini_key": "hash_verification_enabled",
        "default": "true",
        "is_secret": False,
    },
    # --- Admin-enforced 2FA requirement ---
    "totp_required": {
        "section": "security",
        "ini_key": "totp_required",
        "default": "false",
        "is_secret": False,
    },
    # --- UI preferences ---
    "show_opensource_catalogue": {
        "section": "ui",
        "ini_key": "show_opensource_catalogue",
        "default": "true",
        "is_secret": False,
    },
    "default_search_mode": {
        "section": "ui",
        "ini_key": "default_search_mode",
        "default": "standard",
        "is_secret": False,
    },
    # --- ClamAV local daemon integration ---
    "clamav_enabled": {
        "section": "hash_sources",
        "ini_key": "clamav_enabled",
        "default": "false",
        "is_secret": False,
    },
    "clamav_socket": {
        "section": "hash_sources",
        "ini_key": "clamav_socket",
        "default": "/var/run/clamav/clamd.ctl",
        "is_secret": False,
    },
    "clamav_host": {
        "section": "hash_sources",
        "ini_key": "clamav_host",
        "default": "",
        "is_secret": False,
    },
    "clamav_port": {
        "section": "hash_sources",
        "ini_key": "clamav_port",
        "default": "3310",
        "is_secret": False,
    },
    # --- AI assistant ---
    "ai_enabled": {
        "section": "ai",
        "ini_key": "enabled",
        "default": "false",
        "is_secret": False,
    },
    "ai_provider": {
        "section": "ai",
        "ini_key": "provider",
        "default": "openai",
        "is_secret": False,
    },
    "ai_api_key": {
        "section": "ai",
        "ini_key": "api_key",
        "default": "",
        "is_secret": True,
    },
    "ai_base_url": {
        "section": "ai",
        "ini_key": "base_url",
        "default": "https://api.openai.com/v1",
        "is_secret": False,
    },
    "ai_model": {
        "section": "ai",
        "ini_key": "model",
        "default": "gpt-4o-mini",
        "is_secret": False,
    },
    "ai_rate_limit_per_hour": {
        "section": "ai",
        "ini_key": "rate_limit_per_hour",
        "default": "20",
        "is_secret": False,
    },
    # --- Staging queue auto-cleanup ---
    "staging_auto_cleanup_days": {
        "section": "scheduler",
        "ini_key": "staging_auto_cleanup_days",
        "default": "0",
        "is_secret": False,
    },
    # --- Push notifications (Web Push / VAPID) ---
    "push_notifications_enabled": {
        "section": "notifications",
        "ini_key": "push_enabled",
        "default": "false",
        "is_secret": False,
    },
    "push_vapid_public_key": {
        "section": "notifications",
        "ini_key": "push_vapid_public_key",
        "default": "",
        "is_secret": False,
    },
    "push_vapid_private_key": {
        "section": "notifications",
        "ini_key": "push_vapid_private_key",
        "default": "",
        "is_secret": True,
    },
    "push_vapid_claims_sub": {
        "section": "notifications",
        "ini_key": "push_vapid_claims_sub",
        "default": "mailto:admin@example.com",
        "is_secret": False,
    },
    # --- Quick approve mobile mode ---
    "quick_approve_mode_enabled": {
        "section": "ui",
        "ini_key": "quick_approve_mode_enabled",
        "default": "false",
        "is_secret": False,
    },
}

_INDEXER_PREFIX = "indexer:"


def _mask_secret(value: str) -> str:
    """Mask a secret value for API display."""
    if not value:
        return ""
    return "****" + value[-4:] if len(value) > 4 else "****"


class IniSettingsManager:
    """Thread-safe INI file-backed settings manager."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._ensure_file_exists()

    # ------------------------------------------------------------------
    # File I/O
    # ------------------------------------------------------------------

    def _load(self) -> configparser.ConfigParser:
        """Read and parse the INI file. Must be called under self._lock."""
        config = configparser.ConfigParser()
        config.read(self._path, encoding="utf-8")
        return config

    def _save(self, config: configparser.ConfigParser) -> None:
        """Atomically write the INI file. Must be called under self._lock."""
        parent = self._path.parent
        fd, tmp_path = tempfile.mkstemp(
            dir=parent, prefix=".softarr_ini_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                config.write(f)
            os.replace(tmp_path, self._path)
        except BaseException:
            # Clean up the temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        # Enforce restrictive permissions (owner read/write only)
        try:
            os.chmod(self._path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass

    def _ensure_file_exists(self) -> None:
        """Create softarr.ini with defaults if it does not exist."""
        if self._path.exists():
            return
        config = configparser.ConfigParser()
        config["misc"] = {"version": "1"}
        config["sabnzbd"] = {
            "url": "",
            "api_key": "",
            "category": "software",
            "ssl_verify": "true",
            "timeout": "30",
        }
        config["adapters"] = {
            "github_enabled": "true",
            "vendor_enabled": "true",
            "usenet_enabled": "false",
        }
        with self._lock:
            # Double-check after acquiring lock
            if not self._path.exists():
                self._path.parent.mkdir(parents=True, exist_ok=True)
                self._save(config)

    # ------------------------------------------------------------------
    # Flat settings (key-value)
    # ------------------------------------------------------------------

    def get(self, key: str) -> Optional[str]:
        """Get a setting value by key. Returns the default if not set."""
        defn = SETTING_DEFINITIONS.get(key)
        if not defn:
            return None
        with self._lock:
            config = self._load()
        section = defn["section"]
        ini_key = defn["ini_key"]
        if config.has_option(section, ini_key):
            return config.get(section, ini_key)
        return defn["default"]

    def set(self, key: str, value: str) -> None:
        """Set a setting value."""
        defn = SETTING_DEFINITIONS.get(key)
        if not defn:
            msg = f"Unknown setting key: {key}"
            raise ValueError(msg)
        with self._lock:
            config = self._load()
            section = defn["section"]
            if not config.has_section(section):
                config.add_section(section)
            config.set(section, defn["ini_key"], value)
            self._save(config)

    def get_all_masked(self) -> Dict[str, Any]:
        """Get all settings with secrets masked for API/UI display."""
        with self._lock:
            config = self._load()
        output: Dict[str, Any] = {}
        for key, defn in SETTING_DEFINITIONS.items():
            section = defn["section"]
            ini_key = defn["ini_key"]
            if config.has_option(section, ini_key):
                value = config.get(section, ini_key)
            else:
                value = defn["default"]
            if defn["is_secret"] and value:
                output[key] = _mask_secret(value)
            else:
                output[key] = value
            output[f"{key}_is_set"] = bool(value)
        return output

    # ------------------------------------------------------------------
    # Indexer CRUD
    # ------------------------------------------------------------------

    def _indexer_section(self, name: str) -> str:
        return f"{_INDEXER_PREFIX}{name}"

    def _parse_indexer(
        self, config: configparser.ConfigParser, section: str
    ) -> Dict[str, Any]:
        """Parse an indexer section into a dict."""
        name = section[len(_INDEXER_PREFIX) :]
        return {
            "name": name,
            "url": config.get(section, "url", fallback=""),
            "api_key": config.get(section, "api_key", fallback=""),
            "enabled": config.getboolean(section, "enabled", fallback=True),
            "priority": config.getint(section, "priority", fallback=0),
            "categories": config.get(
                section,
                "categories",
                fallback="4000,4010,4020,4030,4040,4050,4060,4070",
            ),
            # type distinguishes Newznab (NZB/Usenet) from Torznab (torrent).
            # Defaults to "newznab" for backwards compatibility.
            "type": config.get(section, "type", fallback="newznab"),
        }

    def get_indexers(self) -> List[Dict[str, Any]]:
        """Get all indexers sorted by priority then name."""
        with self._lock:
            config = self._load()
        indexers = []
        for section in config.sections():
            if section.startswith(_INDEXER_PREFIX):
                indexers.append(self._parse_indexer(config, section))
        indexers.sort(key=lambda x: (x["priority"], x["name"]))
        return indexers

    def get_indexer(self, name: str) -> Optional[Dict[str, Any]]:
        """Get a single indexer by name."""
        section = self._indexer_section(name)
        with self._lock:
            config = self._load()
        if not config.has_section(section):
            return None
        return self._parse_indexer(config, section)

    def create_indexer(
        self,
        name: str,
        url: str,
        api_key: str,
        enabled: bool = True,
        priority: int = 0,
        categories: str = "4000,4010,4020,4030,4040,4050,4060,4070",
        type: str = "newznab",
    ) -> Dict[str, Any]:
        """Create a new indexer. Raises ValueError if name already exists.

        Args:
            type: Protocol type -- "newznab" (default, Usenet/NZB) or "torznab"
                  (torrent). Used to route searches to the correct adapter.
        """
        section = self._indexer_section(name)
        with self._lock:
            config = self._load()
            if config.has_section(section):
                msg = f"Indexer already exists: {name}"
                raise ValueError(msg)
            config.add_section(section)
            config.set(section, "url", url)
            config.set(section, "api_key", api_key)
            config.set(section, "enabled", str(enabled).lower())
            config.set(section, "priority", str(priority))
            config.set(section, "categories", categories)
            config.set(section, "type", type)
            self._save(config)
        return {
            "name": name,
            "url": url,
            "api_key": api_key,
            "enabled": enabled,
            "priority": priority,
            "categories": categories,
            "type": type,
        }

    def update_indexer(
        self, current_name: str, **updates: Any
    ) -> Optional[Dict[str, Any]]:
        """Partially update an indexer. Returns None if not found.

        Supports renaming via the ``name`` key in updates.
        """
        section = self._indexer_section(current_name)
        with self._lock:
            config = self._load()
            if not config.has_section(section):
                return None

            new_name = updates.pop("name", None)
            # Apply field updates to the existing section
            for field, value in updates.items():
                if field in ("url", "api_key", "categories", "type"):
                    config.set(section, field, value)
                elif field == "enabled":
                    config.set(section, "enabled", str(value).lower())
                elif field == "priority":
                    config.set(section, "priority", str(value))

            # Handle rename: copy values to new section, remove old
            if new_name and new_name != current_name:
                new_section = self._indexer_section(new_name)
                if config.has_section(new_section):
                    msg = f"Indexer already exists: {new_name}"
                    raise ValueError(msg)
                config.add_section(new_section)
                for key, value in config.items(section):
                    config.set(new_section, key, value)
                config.remove_section(section)
                section = new_section

            self._save(config)
            return self._parse_indexer(config, section)

    def delete_indexer(self, name: str) -> bool:
        """Delete an indexer. Returns True if deleted, False if not found."""
        section = self._indexer_section(name)
        with self._lock:
            config = self._load()
            if not config.has_section(section):
                return False
            config.remove_section(section)
            self._save(config)
        return True

    def get_enabled_indexer_configs(self) -> List[UsenetIndexerConfig]:
        """Load enabled Newznab (Usenet/NZB) indexers as adapter config objects.

        Filters to indexers with type=newznab (or no type set, for backwards
        compatibility). Torznab indexers are excluded -- use
        get_enabled_torznab_configs() for those.
        """
        indexers = self.get_indexers()
        return [
            UsenetIndexerConfig(
                name=idx["name"],
                url=idx["url"],
                api_key=idx["api_key"],
                enabled=True,
                categories=idx.get(
                    "categories", "4000,4010,4020,4030,4040,4050,4060,4070"
                ),
            )
            for idx in indexers
            if idx["enabled"] and idx.get("type", "newznab") == "newznab"
        ]

    def get_enabled_torznab_configs(self) -> List[UsenetIndexerConfig]:
        """Load enabled Torznab (torrent) indexers as adapter config objects.

        Filters to indexers with type=torznab only.
        """
        indexers = self.get_indexers()
        return [
            UsenetIndexerConfig(
                name=idx["name"],
                url=idx["url"],
                api_key=idx["api_key"],
                enabled=True,
                categories=idx.get(
                    "categories", "4000,4010,4020,4030,4040,4050,4060,4070"
                ),
            )
            for idx in indexers
            if idx["enabled"] and idx.get("type") == "torznab"
        ]

    # ------------------------------------------------------------------
    # Indexer health stats
    # ------------------------------------------------------------------

    def record_indexer_result(
        self, name: str, success: bool, response_time_ms: int
    ) -> None:
        """Record the result of an indexer query (success/failure and response time).

        Stats are stored directly in the per-indexer INI section so no
        additional database table is needed.
        """
        section = self._indexer_section(name)
        from datetime import datetime, timezone

        now_iso = datetime.now(timezone.utc).isoformat()
        with self._lock:
            config = self._load()
            if not config.has_section(section):
                return  # Indexer doesn't exist yet -- skip
            # Increment counters
            if success:
                current = config.getint(section, "success_count", fallback=0)
                config.set(section, "success_count", str(current + 1))
                config.set(section, "last_success_at", now_iso)
            else:
                current = config.getint(section, "failure_count", fallback=0)
                config.set(section, "failure_count", str(current + 1))
                config.set(section, "last_failure_at", now_iso)
            config.set(section, "last_response_ms", str(response_time_ms))
            self._save(config)

    def get_indexer_stats(self, name: str) -> Dict[str, Any]:
        """Return health stats for a single indexer."""
        section = self._indexer_section(name)
        with self._lock:
            config = self._load()
        if not config.has_section(section):
            return {
                "success_count": 0,
                "failure_count": 0,
                "last_success_at": None,
                "last_failure_at": None,
                "last_response_ms": 0,
            }
        return {
            "success_count": config.getint(section, "success_count", fallback=0),
            "failure_count": config.getint(section, "failure_count", fallback=0),
            "last_success_at": config.get(section, "last_success_at", fallback=None),
            "last_failure_at": config.get(section, "last_failure_at", fallback=None),
            "last_response_ms": config.getint(section, "last_response_ms", fallback=0),
        }


# ---------------------------------------------------------------------------
# Singleton and FastAPI dependency
# ---------------------------------------------------------------------------

_manager: Optional[IniSettingsManager] = None


def get_ini_settings() -> IniSettingsManager:
    """FastAPI dependency that returns the singleton IniSettingsManager."""
    global _manager  # noqa: PLW0603
    if _manager is None:
        from softarr.core.config import settings

        _manager = IniSettingsManager(settings.ini_path)
    return _manager


def reset_ini_settings() -> None:
    """Reset the singleton (for testing)."""
    global _manager  # noqa: PLW0603
    _manager = None
