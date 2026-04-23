from abc import ABC, abstractmethod
from typing import Any, Dict, List

from pydantic import BaseModel


class ReleaseSearchResult(BaseModel):
    name: str
    display_name: str | None = (
        None  # Cleaned title for UI display (raw title preserved in name)
    )
    version: str
    supported_os: List[str]
    architecture: str | None = None
    publisher: str | None = None
    source_type: str
    source_origin: str
    raw_data: Dict[str, Any] = {}


class BaseAdapter(ABC):
    name: str
    source_type: str

    @abstractmethod
    async def search_releases(
        self, software: Dict, query: str | None = None
    ) -> List[ReleaseSearchResult]:
        """Search for releases matching the software definition."""
        pass

    @abstractmethod
    async def fetch_release_details(self, release_url: str) -> Dict:
        """Fetch detailed metadata for a specific release."""
        pass
