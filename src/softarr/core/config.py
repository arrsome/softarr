from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./dev/softarr.db"

    # Config directory (for softarr.ini); defaults to current working directory
    CONFIG_DIR: str = ""

    # Security
    SECRET_KEY: str = "change-this-in-production"
    DEBUG: bool = False
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD_HASH: str = ""  # bcrypt hash; set via CLI or env
    ADMIN_DEFAULT_PASSWORD: str = (
        "admin"  # plaintext default; override or change after first login
    )
    SESSION_MAX_AGE_SECONDS: int = 86400  # 24 hours

    # CORS -- restrict in production
    CORS_ORIGINS: List[str] = ["http://localhost:8000"]
    CORS_ALLOW_ALL: bool = False  # only True for local dev

    # GitHub adapter
    GITHUB_TOKEN: str = ""

    # SABnzbd integration (boot defaults; production values go in DB)
    SABNZBD_URL: str = ""
    SABNZBD_API_KEY: str = ""
    SABNZBD_CATEGORY: str = "software"

    # Rate limiting
    RATE_LIMIT_SEARCH: str = "10/minute"
    RATE_LIMIT_DEFAULT: str = "60/minute"

    # Auto-create tables on startup (dev convenience; use migrations in prod)
    AUTO_CREATE_TABLES: bool = True

    # Logging format: "text" (human-readable) or "json" (structured)
    LOG_FORMAT: str = "text"

    model_config = {"env_file": ".env"}

    @property
    def ini_path(self) -> Path:
        """Resolved path to softarr.ini."""
        base = Path(self.CONFIG_DIR) if self.CONFIG_DIR else Path(".")
        return base / "softarr.ini"


settings = Settings()
