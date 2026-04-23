"""First-boot initialisation command.

Runs once before the web server starts. Creates database tables (when
``AUTO_CREATE_TABLES`` is set), initialises ``softarr.ini`` with defaults
if missing, and bootstraps the default admin user. Designed to be the
ENTRYPOINT step in the container so gunicorn workers never race on
first-boot setup.

Invoked via the ``softarr-init`` console script registered in
``pyproject.toml``.

Flags:
    --skip-db      skip ``Base.metadata.create_all``
    --skip-ini     skip INI file initialisation
    --skip-admin   skip admin user bootstrap

Exit codes:
    0 -- success (including idempotent no-op runs)
    1 -- unexpected error (logged with traceback)
"""

import argparse
import asyncio
import sys

from softarr.auth.service import AuthService
from softarr.core.config import settings
from softarr.core.database import AsyncSessionLocal, Base, engine
from softarr.core.ini_settings import get_ini_settings
from softarr.core.logging import configure_logging


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="softarr-init",
        description=(
            "Softarr first-boot initialisation. Creates tables, initialises "
            "softarr.ini, and bootstraps the default admin user. Safe to run "
            "repeatedly -- idempotent when the DB is already set up."
        ),
    )
    parser.add_argument(
        "--skip-db",
        action="store_true",
        help="Skip Base.metadata.create_all. Use when Alembic owns the schema.",
    )
    parser.add_argument(
        "--skip-ini",
        action="store_true",
        help="Skip softarr.ini initialisation.",
    )
    parser.add_argument(
        "--skip-admin",
        action="store_true",
        help="Skip default admin user bootstrap.",
    )
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> None:
    """Perform first-boot setup steps in order.

    Each step is gated by its ``--skip-*`` flag. Steps log their own
    progress; this function just orchestrates and lets exceptions bubble.
    """
    logger = configure_logging()

    # Warn loudly if the SECRET_KEY is still the insecure default value.
    if settings.SECRET_KEY == "change-this-in-production":
        logger.warning(
            "========================================\n"
            "  SECURITY WARNING\n"
            "  SECRET_KEY is set to the default value.\n"
            "  Set a random 64-byte SECRET_KEY in .env before going to production.\n"
            '  Example: python -c "import secrets; print(secrets.token_hex(32))"\n'
            "========================================"
        )

    # 1. Database tables.
    if args.skip_db:
        logger.info("softarr-init: --skip-db set, not creating tables")
    elif settings.AUTO_CREATE_TABLES:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info(
            "softarr-init: auto-created database tables (AUTO_CREATE_TABLES=true)"
        )
    else:
        logger.info("softarr-init: AUTO_CREATE_TABLES is false, not creating tables")

    # 2. INI settings file.
    if args.skip_ini:
        logger.info("softarr-init: --skip-ini set, not initialising softarr.ini")
    else:
        ini = get_ini_settings()
        logger.info("softarr-init: settings loaded from %s", ini._path)

    # 3. Admin user bootstrap.
    if args.skip_admin:
        logger.info("softarr-init: --skip-admin set, not creating admin user")
        return

    async with AsyncSessionLocal() as db:
        auth = AuthService(db)
        generated_password = await auth.bootstrap_admin()
        if generated_password:
            logger.warning(
                "========================================\n"
                "  DEFAULT ADMIN CREDENTIALS\n"
                "  Username : %s\n"
                "  Password : %s\n"
                "\n"
                "  This is the DEFAULT password. Change it immediately\n"
                "  via Settings > Users after your first login.\n"
                "========================================",
                settings.ADMIN_USERNAME,
                generated_password,
            )
        else:
            logger.info("softarr-init: users already exist, skipping admin bootstrap")


async def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        await run(args)
    except Exception:
        # Configure logging in case run() failed before it did.
        configure_logging().exception("softarr-init failed")
        return 1
    return 0


def cli() -> None:
    """Synchronous entry point for the ``softarr-init`` console script."""
    sys.exit(asyncio.run(main()))
