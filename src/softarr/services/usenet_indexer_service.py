"""Service for managing Usenet indexer configurations.

Provides CRUD operations backed by softarr.ini [indexer:*] sections
and an internal method to load enabled indexer configs for the UsenetAdapter.
"""

from typing import List, Optional

from softarr.adapters.usenet import UsenetIndexerConfig
from softarr.core.ini_settings import IniSettingsManager
from softarr.schemas.usenet_indexer import (
    UsenetIndexerCreate,
    UsenetIndexerResponse,
    UsenetIndexerUpdate,
)


class UsenetIndexerService:
    def __init__(self, ini: IniSettingsManager):
        self.ini = ini

    # ------------------------------------------------------------------
    # Public CRUD (all responses have masked API keys)
    # ------------------------------------------------------------------

    def create(self, data: UsenetIndexerCreate) -> UsenetIndexerResponse:
        """Create a new indexer and return the masked response."""
        raw = self.ini.create_indexer(
            name=data.name,
            url=data.url,
            api_key=data.api_key,
            enabled=data.enabled,
            priority=data.priority,
            categories=data.categories,
            type=data.type,
        )
        return self._to_response(raw)

    def get_all(self) -> List[UsenetIndexerResponse]:
        """List all indexers ordered by priority, with masked API keys."""
        return [self._to_response(idx) for idx in self.ini.get_indexers()]

    def get_by_name(self, name: str) -> Optional[UsenetIndexerResponse]:
        """Get a single indexer by name, or None if not found."""
        raw = self.ini.get_indexer(name)
        if not raw:
            return None
        return self._to_response(raw)

    def update(
        self, name: str, data: UsenetIndexerUpdate
    ) -> Optional[UsenetIndexerResponse]:
        """Partially update an indexer. Returns None if not found."""
        updates = data.model_dump(exclude_unset=True)
        raw = self.ini.update_indexer(name, **updates)
        if not raw:
            return None
        return self._to_response(raw)

    def delete(self, name: str) -> bool:
        """Delete an indexer. Returns True if deleted, False if not found."""
        return self.ini.delete_indexer(name)

    # ------------------------------------------------------------------
    # Internal -- used by ReleaseService to build the UsenetAdapter
    # ------------------------------------------------------------------

    def get_all_enabled_configs(self) -> List[UsenetIndexerConfig]:
        """Load all enabled indexers as adapter config objects (unmasked)."""
        return self.ini.get_enabled_indexer_configs()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _to_response(self, raw: dict) -> UsenetIndexerResponse:
        """Convert an indexer dict to a response with a masked API key."""
        return UsenetIndexerResponse(
            name=raw["name"],
            url=raw["url"],
            api_key=self._mask_api_key(raw["api_key"]),
            enabled=raw["enabled"],
            priority=raw["priority"],
            categories=raw.get("categories", "4000,4010,4020,4030,4040,4050,4060,4070"),
            type=raw.get("type", "newznab"),
        )

    @staticmethod
    def _mask_api_key(key: str) -> str:
        """Mask an API key for display. Matches IniSettingsManager convention."""
        if not key:
            return ""
        return "****" + key[-4:] if len(key) > 4 else "****"
