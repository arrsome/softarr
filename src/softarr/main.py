import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy.ext.asyncio import AsyncSession

from softarr.api.v1 import actions as actions_router
from softarr.api.v1 import ai as ai_router
from softarr.api.v1 import auth_routes as api_auth_router
from softarr.api.v1 import hooks as hooks_router
from softarr.api.v1 import indexers as indexers_router
from softarr.api.v1 import metrics as metrics_router
from softarr.api.v1 import push as push_router
from softarr.api.v1 import releases, software, staging
from softarr.api.v1 import settings as settings_router
from softarr.api.v1 import users as users_router
from softarr.auth import routes as auth_routes
from softarr.auth.service import AuthService
from softarr.auth.sessions import get_session_data
from softarr.core.config import settings
from softarr.core.database import AsyncSessionLocal, get_db
from softarr.core.ini_settings import IniSettingsManager, get_ini_settings
from softarr.core.logging import configure_logging
from softarr.middleware.csrf import CSRFMiddleware, get_csrf_token
from softarr.middleware.rate_limit import limiter
from softarr.services.contributor_service import ContributorService
from softarr.services.release_service import ReleaseService
from softarr.services.software_service import SoftwareService
from softarr.version import __version__ as APP_VERSION

logger = configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ----- startup -----
    #
    # First-boot setup (table creation, INI defaults, admin bootstrap) lives
    # in the ``softarr-init`` console script and must run BEFORE gunicorn
    # starts. Running it inside the lifespan caused a race on multi-worker
    # setups where every worker tried to insert the default admin row. The
    # container entrypoint now invokes ``softarr-init`` first; this lifespan
    # only handles runtime concerns (background tasks, worker-local state).

    # Fast-fail if the DB has never been initialised. Catches the
    # "forgot to run softarr-init" misconfiguration loudly instead of
    # limping along with no users.
    async with AsyncSessionLocal() as db:
        auth = AuthService(db)
        if await auth.user_count() == 0:
            logger.error(
                "No users found in database. Run `softarr-init` before "
                "starting the web server, or ensure your container "
                "entrypoint invokes it."
            )
            raise RuntimeError("Database not initialised -- run softarr-init first")

    # Start retention background task if enabled
    ini = get_ini_settings()
    if (ini.get("retention_enabled") or "false").lower() == "true":
        task = asyncio.ensure_future(_retention_loop())
        app.state.retention_task = task
        logger.info("Release retention background task started")

    # Start hash recheck background task
    task = asyncio.ensure_future(_hash_recheck_loop())
    app.state.hash_recheck_task = task
    logger.info("Hash intelligence recheck background task started")

    # Start scheduler if enabled
    if (ini.get("scheduler_enabled") or "false").lower() == "true":
        from softarr.services.scheduler_service import SchedulerService

        scheduler = SchedulerService(ini, AsyncSessionLocal)
        scheduler.start()
        app.state.scheduler = scheduler
        logger.info("Release scheduler started")

    # Start backup background task if enabled
    if (ini.get("backup_enabled") or "false").lower() == "true":
        backup_task = asyncio.ensure_future(_backup_loop())
        app.state.backup_task = backup_task
        logger.info("Backup background task started")

    # Start qBittorrent completion poll task (always running; skips when inactive)
    qbt_poll_task = asyncio.ensure_future(_qbittorrent_poll_loop())
    app.state.qbittorrent_poll_task = qbt_poll_task
    logger.info("qBittorrent completion poll task started")

    logger.info("Softarr v%s started successfully", APP_VERSION)

    yield

    # ----- shutdown -----
    for attr in (
        "retention_task",
        "scheduler_task",
        "hash_recheck_task",
        "backup_task",
        "qbittorrent_poll_task",
    ):
        task = getattr(app.state, attr, None)
        if task and not task.done():
            task.cancel()


app = FastAPI(
    title="Softarr",
    description=(
        "Software Release Manager -- ARR-style pipeline for tracking, analysing, and safely "
        "managing software releases.\n\n"
        "## Authentication\n\n"
        "All endpoints require authentication via one of:\n"
        "- **Session cookie** -- obtained by posting credentials to `POST /auth/login`.\n"
        "- **X-Api-Key header** -- static key configured in `softarr.ini` under `[security] api_key`.\n\n"
        "## Workflow States\n\n"
        "`DISCOVERED` -> `STAGED` -> `UNDER_REVIEW` -> `APPROVED` -> `QUEUED_FOR_DOWNLOAD` -> `DOWNLOADED`\n\n"
        "Releases can also be `REJECTED` at any point and returned to `STAGED`."
    ),
    version=APP_VERSION,
    debug=settings.DEBUG,
    lifespan=lifespan,
    openapi_tags=[
        {"name": "auth", "description": "Authentication and session management."},
        {
            "name": "software",
            "description": "Software library -- tracked entries with publisher, OS, and adapter preferences.",
        },
        {
            "name": "releases",
            "description": "Release discovery, analysis, and workflow management.",
        },
        {
            "name": "staging",
            "description": "Workflow state transitions (approve, reject, override).",
        },
        {
            "name": "actions",
            "description": "Download client actions -- send to SABnzbd, upload NZB, test connections.",
        },
        {
            "name": "settings",
            "description": "Runtime settings, audit log, and system health.",
        },
        {
            "name": "indexers",
            "description": "Usenet/Newznab indexer CRUD and caps detection.",
        },
        {
            "name": "hooks",
            "description": "Inbound webhook receiver for download client callbacks.",
        },
    ],
)

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

# CORS -- restricted by default; fully open only when explicitly configured
if settings.CORS_ALLOW_ALL and settings.DEBUG:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "Authorization", "X-CSRF-Token"],
    )

# CSRF protection for browser sessions
app.add_middleware(CSRFMiddleware)

# Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ---------------------------------------------------------------------------
# Security headers
# ---------------------------------------------------------------------------


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Inject standard security headers on every response."""
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    if not settings.DEBUG:
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
    return response


# Static files
_PKG_DIR = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=_PKG_DIR / "static"), name="static")
templates = Jinja2Templates(directory=str(_PKG_DIR / "templates"))

# Register i18n translation function as a Jinja2 global so all templates can
# call {{ t('key', lang) }} without needing an explicit import.
from softarr.core.i18n import t as _i18n_t  # noqa: E402

templates.env.globals["t"] = _i18n_t

# ---------------------------------------------------------------------------
# API routers
# ---------------------------------------------------------------------------

app.include_router(auth_routes.router, prefix="/auth", tags=["auth"])
app.include_router(api_auth_router.router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(software.router, prefix="/api/v1/software", tags=["software"])
app.include_router(releases.router, prefix="/api/v1/releases", tags=["releases"])
app.include_router(staging.router, prefix="/api/v1/staging", tags=["staging"])
app.include_router(settings_router.router, prefix="/api/v1/settings", tags=["settings"])
app.include_router(actions_router.router, prefix="/api/v1/actions", tags=["actions"])
app.include_router(indexers_router.router, prefix="/api/v1/indexers", tags=["indexers"])
app.include_router(hooks_router.router, prefix="/api/v1/hooks", tags=["hooks"])
app.include_router(users_router.router, prefix="/api/v1/users", tags=["users"])
app.include_router(ai_router.router, prefix="/api/v1/ai", tags=["ai"])
app.include_router(push_router.router, prefix="/api/v1/push", tags=["push"])
app.include_router(metrics_router.router, tags=["metrics"])


async def _retention_loop():
    """Daily background task: enforce release retention policies.

    - Delete DISCOVERED releases older than ``retention_discovered_days``.
    - Delete REJECTED releases older than ``retention_rejected_days`` when enabled.
    - Keep only the latest N DOWNLOADED releases per software entry when
      ``retention_keep_downloaded_count`` > 0.
    """
    while True:
        await asyncio.sleep(24 * 3600)
        try:
            ini = get_ini_settings()
            async with AsyncSessionLocal() as db:
                from sqlalchemy import select as sa_select

                from softarr.models.software import Software as SoftwareModel

                service = ReleaseService(db, ini)

                # -- Discovered cleanup --
                days = int(ini.get("retention_discovered_days") or "30")
                n = await service.delete_old_discovered(days)
                if n > 0:
                    logger.info("Retention: deleted %d old DISCOVERED releases", n)

                # -- Rejected cleanup --
                if (ini.get("retention_rejected_enabled") or "false").lower() == "true":
                    rej_days = int(ini.get("retention_rejected_days") or "90")
                    n = await service.delete_old_rejected(rej_days)
                    if n > 0:
                        logger.info("Retention: deleted %d old REJECTED releases", n)

                # -- Keep only latest N downloads per software --
                keep_count = int(ini.get("retention_keep_downloaded_count") or "0")
                if keep_count > 0:
                    result = await db.execute(sa_select(SoftwareModel.id))
                    sw_ids = [row[0] for row in result.all()]
                    total_purged = 0
                    for sw_id in sw_ids:
                        total_purged += await service.keep_latest_downloaded(
                            sw_id, keep_count
                        )
                    if total_purged > 0:
                        logger.info(
                            "Retention: purged %d old DOWNLOADED releases (keeping last %d per entry)",
                            total_purged,
                            keep_count,
                        )

                # -- Audit log retention --
                audit_retention = int(ini.get("audit_retention_days") or "365")
                if audit_retention > 0:
                    from softarr.services.audit_service import AuditService

                    audit_svc = AuditService(db)
                    n = await audit_svc.prune_old_logs(audit_retention)
                    if n > 0:
                        logger.info("Retention: pruned %d old audit log entries", n)

        except Exception as exc:
            logger.error("Retention task error: %s", exc)


async def _hash_recheck_loop():
    """Periodic background task: recheck hash intelligence records with unknown verdict."""
    while True:
        try:
            ini = get_ini_settings()
            interval_hours = int(ini.get("hash_recheck_interval_hours") or "24")
            await asyncio.sleep(interval_hours * 3600)
            from softarr.services.hash_intelligence_service import (
                HashIntelligenceService,
            )

            async with AsyncSessionLocal() as db:
                svc = HashIntelligenceService(db, ini)
                n = await svc.recheck_unknown()
                if n > 0:
                    logger.info("Hash recheck: rechecked %d unknown records", n)
        except Exception as exc:
            logger.error("Hash recheck task error: %s", exc)


async def _backup_loop():
    """Periodic background task: copy softarr.ini and SQLite DB to backup_dir."""
    while True:
        try:
            ini = get_ini_settings()
            interval_hours = int(ini.get("backup_interval_hours") or "24")
            await asyncio.sleep(interval_hours * 3600)
            from softarr.services.backup_service import BackupService

            svc = BackupService(ini)
            result = await svc.run_backup()
            if result["status"] == "ok":
                logger.info(
                    "Backup: created %d file(s), pruned %d old backup(s)",
                    len(result.get("files", [])),
                    result.get("pruned", 0),
                )
            elif result["status"] == "error":
                logger.error("Backup failed: %s", result.get("error"))
        except Exception as exc:
            logger.error("Backup task error: %s", exc)


async def _qbittorrent_poll_loop():
    """Periodic background task: poll qBittorrent for completed downloads.

    Runs continuously but is a no-op when the active download client is not
    qBittorrent. On each tick it queries all QUEUED_FOR_DOWNLOAD releases
    whose download_client_id starts with 'qbt:' and checks each against the
    qBittorrent API. Completed torrents are transitioned to DOWNLOADED;
    failed ones to DOWNLOAD_FAILED.
    """
    from sqlalchemy import select as sa_select

    from softarr.integrations.qbittorrent import (
        QBIT_HASH_PREFIX,
        QBittorrentClient,
        QBittorrentConfig,
        QBittorrentError,
    )
    from softarr.models.release import Release as ReleaseModel
    from softarr.models.release import WorkflowState
    from softarr.services.audit_service import AuditService
    from softarr.services.release_service import ReleaseService

    while True:
        try:
            ini = get_ini_settings()
            interval = int(ini.get("qbittorrent_poll_interval_seconds") or "60")
            await asyncio.sleep(interval)

            active = (ini.get("active_download_client") or "sabnzbd").lower()
            if active != "qbittorrent":
                continue

            url = ini.get("qbittorrent_url") or ""
            username = ini.get("qbittorrent_username") or ""
            password = ini.get("qbittorrent_password") or ""
            if not url or not username:
                continue

            config = QBittorrentConfig(
                url=url,
                username=username,
                password=password,
                category=ini.get("qbittorrent_category") or "software",
                ssl_verify=(ini.get("qbittorrent_ssl_verify") or "true").lower()
                == "true",
                timeout=int(ini.get("qbittorrent_timeout") or "30"),
            )
            client = QBittorrentClient(config)

            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    sa_select(ReleaseModel).where(
                        ReleaseModel.workflow_state
                        == WorkflowState.QUEUED_FOR_DOWNLOAD,
                        ReleaseModel.download_client_id.like(f"{QBIT_HASH_PREFIX}%"),
                    )
                )
                queued = result.scalars().all()

                if not queued:
                    continue

                for release in queued:
                    hash_hex = (release.download_client_id or "")[
                        len(QBIT_HASH_PREFIX) :
                    ]
                    if not hash_hex:
                        continue

                    try:
                        info = await client.get_torrent_info(hash_hex)
                    except QBittorrentError as exc:
                        logger.warning(
                            "qBittorrent poll: error fetching info for hash %s: %s",
                            hash_hex,
                            exc,
                        )
                        continue

                    from softarr.integrations.qbittorrent import (
                        _COMPLETED_STATES,
                        _FAILED_STATES,
                    )

                    if info is None:
                        # Torrent removed from client -- assume completed/seeded
                        target = WorkflowState.DOWNLOADED
                        state_str = "removed"
                    else:
                        state_str = (info.get("state") or "").lower()
                        if state_str in {s.lower() for s in _COMPLETED_STATES}:
                            target = WorkflowState.DOWNLOADED
                        elif state_str in {s.lower() for s in _FAILED_STATES}:
                            target = WorkflowState.DOWNLOAD_FAILED
                        else:
                            continue  # Still in progress

                    release_service = ReleaseService(db, ini)
                    try:
                        await release_service.transition_state(
                            release.id,
                            target,
                            changed_by="poller:qbittorrent",
                        )
                    except ValueError as exc:
                        logger.warning(
                            "qBittorrent poll: state transition error for %s: %s",
                            release.id,
                            exc,
                        )
                        continue

                    audit = AuditService(db)
                    await audit.log_action(
                        "download_complete"
                        if target == WorkflowState.DOWNLOADED
                        else "download_failed",
                        "release",
                        release.id,
                        user="poller:qbittorrent",
                        details={"hash": hash_hex, "state": state_str},
                    )

                    logger.info(
                        "qBittorrent poll: transitioned release %s (%s %s) to %s",
                        release.id,
                        release.name,
                        release.version,
                        target.value,
                    )

                    if target == WorkflowState.DOWNLOADED:
                        try:
                            from softarr.services.notification_service import (
                                NotificationService,
                            )

                            notif = NotificationService(ini)
                            asyncio.ensure_future(
                                notif.notify(
                                    "download_complete",
                                    {
                                        "name": release.name,
                                        "version": release.version,
                                        "release_id": str(release.id),
                                        "client": "qbittorrent",
                                    },
                                )
                            )
                        except Exception as exc:
                            logger.warning(
                                "qBittorrent poll: notification error: %s", exc
                            )

        except Exception as exc:
            logger.error("qBittorrent poll task error: %s", exc)


# ---------------------------------------------------------------------------
# Template helpers
# ---------------------------------------------------------------------------


def _template_context(request: Request, title: str, active_page: str, **kwargs):
    """Build standard template context with auth, CSRF, and language preference."""
    user = get_session_data(request)
    ctx = {
        "request": request,
        "title": title,
        "active_page": active_page,
        "current_user": user,
        "csrf_token": get_csrf_token(request),
        "app_version": APP_VERSION,
        "lang": getattr(request.state, "lang", "en"),
    }
    ctx.update(kwargs)
    return ctx


# ---------------------------------------------------------------------------
# Language middleware -- attach user language preference to request state
# ---------------------------------------------------------------------------


@app.middleware("http")
async def language_middleware(request: Request, call_next):
    """Attach the authenticated user's language preference to ``request.state.lang``.

    Falls back to English on any error or when the user is not authenticated.
    Creates a short-lived DB session only for authenticated browser requests.
    """
    request.state.lang = "en"  # safe default
    user = get_session_data(request)
    if user:
        try:
            from uuid import UUID

            from softarr.auth.service import AuthService

            async with AsyncSessionLocal() as _lang_db:
                _db_user = await AuthService(_lang_db).get_user_by_id(UUID(user["uid"]))
                if _db_user and hasattr(_db_user, "language") and _db_user.language:
                    request.state.lang = _db_user.language
        except Exception:
            pass  # silently fall back to English
    return await call_next(request)


# ---------------------------------------------------------------------------
# Auth gate -- redirect unauthenticated users to login
# ---------------------------------------------------------------------------


@app.middleware("http")
async def auth_gate(request: Request, call_next):
    """Redirect unauthenticated browser requests to /login.

    Also enforces the legal disclaimer gate: authenticated users who have not
    yet accepted the disclaimer are redirected to /auth/disclaimer before they
    can access any other page.

    Exemptions: /login, /auth/*, /static/*, /docs, /openapi.json, /health, /ready, /metrics
    """
    path = request.url.path
    exempt = (
        path.startswith("/login")
        or path.startswith("/auth/")
        or path.startswith("/static/")
        or path.startswith("/docs")
        or path.startswith("/openapi")
        or path.startswith("/redoc")
        or path in ("/health", "/ready", "/metrics")
    )
    if exempt:
        return await call_next(request)

    # API routes use header-based auth; let the dependency handle 401
    if path.startswith("/api/"):
        return await call_next(request)

    # Browser routes -- check session
    user = get_session_data(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    # Disclaimer gate -- redirect to acceptance page if not yet accepted
    if not user.get("da", False):
        return RedirectResponse(url="/auth/disclaimer", status_code=303)

    return await call_next(request)


# ---------------------------------------------------------------------------
# PWA static file routes -- served at root so browsers can find them
# ---------------------------------------------------------------------------


@app.get("/manifest.json", include_in_schema=False)
async def pwa_manifest():
    """Web app manifest for PWA installability."""
    from fastapi.responses import FileResponse

    return FileResponse(
        Path(__file__).parent / "static" / "manifest.json",
        media_type="application/manifest+json",
    )


@app.get("/service-worker.js", include_in_schema=False)
async def pwa_service_worker():
    """Service worker script -- must be served at root scope."""
    from fastapi.responses import FileResponse

    return FileResponse(
        Path(__file__).parent / "static" / "js" / "service-worker.js",
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/"},
    )


# ---------------------------------------------------------------------------
# UI Routes
# ---------------------------------------------------------------------------


@app.get("/login")
async def login_page(request: Request):
    user = get_session_data(request)
    if user:
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "title": "Login",
            "csrf_token": get_csrf_token(request),
            "app_version": APP_VERSION,
        },
    )


@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse(
        request, "index.html", _template_context(request, "Dashboard", "dashboard")
    )


@app.get("/software")
async def software_list(
    request: Request,
    page: int = 1,
    page_size: int = 50,
    db=Depends(get_db),
    ini: IniSettingsManager = Depends(get_ini_settings),
):
    import math

    software_service = SoftwareService(db)
    release_service = ReleaseService(db, ini)
    softwares, total = await software_service.get_all_software_paginated(
        page=max(1, page), page_size=max(1, min(200, page_size))
    )
    total_pages = math.ceil(total / page_size) if page_size > 0 else 0

    # Build a map of software_id -> latest downloaded version for the library view
    version_map = {}
    for sw in softwares:
        version_map[str(sw.id)] = await release_service.get_latest_downloaded_version(
            sw.id
        )

    show_catalogue = (ini.get("show_opensource_catalogue") or "true").lower() == "true"
    torrent_enabled = (ini.get("torznab_adapter_enabled") or "false").lower() == "true"

    return templates.TemplateResponse(
        request,
        "software_list.html",
        _template_context(
            request,
            "Software Library",
            "software",
            softwares=softwares,
            version_map=version_map,
            page=page,
            page_size=page_size,
            total=total,
            total_pages=total_pages,
            show_catalogue=show_catalogue,
            torrent_enabled=torrent_enabled,
        ),
    )


@app.get("/software/{software_id}")
async def software_detail(
    request: Request,
    software_id: str,
    db=Depends(get_db),
    ini: IniSettingsManager = Depends(get_ini_settings),
):
    """Software detail page with release timeline."""
    sw_service = SoftwareService(db)
    software_obj = None
    try:
        software_obj = await sw_service.get_software_by_id(UUID(software_id))
    except ValueError, Exception:
        pass
    return templates.TemplateResponse(
        request,
        "software_detail.html",
        _template_context(
            request,
            software_obj.canonical_name if software_obj else "Software",
            "software",
            software=software_obj,
        ),
    )


@app.get("/settings/users")
async def users_page(request: Request):
    """User management page (admin only)."""
    user = get_session_data(request)
    if not user or user.get("role", "admin") != "admin":
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(
        request,
        "users.html",
        _template_context(request, "User Management", "users"),
    )


@app.get("/releases/search")
async def release_search(
    request: Request,
    software_id: str = None,
    db=Depends(get_db),
    ini: IniSettingsManager = Depends(get_ini_settings),
):
    from softarr.services.settings_service import SettingsService

    service = SoftwareService(db)
    softwares = await service.get_all_software()
    svc = SettingsService(ini)
    usenet_on = (svc.get("usenet_adapter_enabled") or "false").lower() == "true"
    torznab_on = (svc.get("torznab_adapter_enabled") or "false").lower() == "true"
    qbt_configured = bool(svc.get("qbittorrent_url") or "")
    default_search_mode = svc.get("default_search_mode") or "standard"
    return templates.TemplateResponse(
        request,
        "release_search.html",
        _template_context(
            request,
            "Release Search",
            "search",
            softwares=softwares,
            software_id=software_id or "",
            usenet_enabled=usenet_on,
            torrent_enabled=torznab_on or qbt_configured,
            default_search_mode=default_search_mode,
        ),
    )


@app.get("/releases/{release_id}")
async def release_detail(
    request: Request,
    release_id: str,
    db=Depends(get_db),
    ini: IniSettingsManager = Depends(get_ini_settings),
):
    service = ReleaseService(db, ini)
    release = None
    try:
        release = await service.get_release_by_id(UUID(release_id))
    except ValueError, Exception:
        pass
    sab_category = ini.get("sabnzbd_category") or "software"
    ai_enabled = (ini.get("ai_enabled") or "false").lower() == "true"
    return templates.TemplateResponse(
        request,
        "release_detail.html",
        _template_context(
            request,
            "Release Detail",
            "search",
            release=release,
            sab_category=sab_category,
            ai_enabled=ai_enabled,
        ),
    )


@app.get("/releases")
async def releases_monitor(
    request: Request,
    db=Depends(get_db),
    ini: IniSettingsManager = Depends(get_ini_settings),
):
    """Release Monitor -- shows all processed releases with trust/safety ratings."""
    sw_service = SoftwareService(db)
    softwares = await sw_service.get_all_software()
    return templates.TemplateResponse(
        request,
        "releases.html",
        _template_context(
            request,
            "Releases",
            "releases",
            softwares=softwares,
        ),
    )


@app.get("/staging")
async def staging_redirect():
    """Redirect legacy /staging URL to /releases."""
    return RedirectResponse(url="/releases", status_code=301)


@app.get("/staging-queue")
async def staging_queue_page(
    request: Request,
    db=Depends(get_db),
    ini: IniSettingsManager = Depends(get_ini_settings),
):
    """Staging queue page -- review, approve, or reject discovered releases."""
    service = ReleaseService(db, ini)
    staging_releases = await service.get_staging_queue()
    discovered_releases = await service.get_discovered_releases()
    return templates.TemplateResponse(
        request,
        "staging_queue.html",
        _template_context(
            request,
            "Staging Queue",
            "staging_queue",
            staging_releases=staging_releases,
            discovered_releases=discovered_releases,
        ),
    )


@app.get("/wanted")
async def wanted_page(
    request: Request,
    db=Depends(get_db),
):
    """Wanted/missing page -- monitored software with no downloaded release."""
    return templates.TemplateResponse(
        request,
        "wanted.html",
        _template_context(request, "Wanted", "wanted"),
    )


@app.get("/history")
async def history_page(request: Request):
    """Activity history timeline page."""
    return templates.TemplateResponse(
        request,
        "history.html",
        _template_context(request, "History", "history"),
    )


@app.get("/change-password")
async def change_password_page(request: Request):
    """Self-service password change page."""
    user = get_session_data(request)
    force_change = bool(user and user.get("fpc"))
    return templates.TemplateResponse(
        request,
        "change_password.html",
        _template_context(
            request,
            "Change Password",
            "",
            force_change=force_change,
        ),
    )


@app.get("/quick-approve")
async def quick_approve_page(
    request: Request,
    db=Depends(get_db),
    ini: IniSettingsManager = Depends(get_ini_settings),
):
    """Quick approve mode -- review one staging item at a time."""
    service = ReleaseService(db, ini)
    queue = await service.get_staging_queue()
    current_release = queue[0] if queue else None
    return templates.TemplateResponse(
        request,
        "quick_approve.html",
        _template_context(
            request,
            "Quick Approve",
            "staging_queue",
            current_release=current_release,
            queue_count=len(queue),
        ),
    )


@app.get("/settings")
async def settings_page(
    request: Request,
    ini: IniSettingsManager = Depends(get_ini_settings),
    db: AsyncSession = Depends(get_db),
):
    from softarr.auth.service import AuthService
    from softarr.auth.sessions import get_session_data
    from softarr.core.config import settings as app_cfg
    from softarr.services.settings_service import SettingsService
    from softarr.services.usenet_indexer_service import UsenetIndexerService

    settings_svc = SettingsService(ini)
    usenet_enabled = (
        settings_svc.get("usenet_adapter_enabled") or "false"
    ).lower() == "true"

    indexer_svc = UsenetIndexerService(ini)
    indexers = indexer_svc.get_all()

    vt_enabled = (settings_svc.get("virustotal_enabled") or "false").lower() == "true"
    nsrl_enabled = (settings_svc.get("nsrl_enabled") or "false").lower() == "true"
    circl_enabled = (
        settings_svc.get("circl_hashlookup_enabled") or "false"
    ).lower() == "true"
    malwarebazaar_enabled_flag = (
        settings_svc.get("malwarebazaar_enabled") or "false"
    ).lower() == "true"
    misp_enabled = (
        settings_svc.get("misp_warninglists_enabled") or "false"
    ).lower() == "true"
    torznab_enabled = (
        settings_svc.get("torznab_adapter_enabled") or "false"
    ).lower() == "true"
    qbt_url = settings_svc.get("qbittorrent_url") or ""
    # qBittorrent is considered configured when a URL has been saved.
    qbt_configured = bool(qbt_url)

    app_settings = {
        "debug": app_cfg.DEBUG,
        "database_type": "sqlite",
        "virustotal_enabled": vt_enabled,
        "nsrl_enabled": nsrl_enabled,
        "circl_hashlookup_enabled": circl_enabled,
        "malwarebazaar_enabled": malwarebazaar_enabled_flag,
        "misp_warninglists_enabled": misp_enabled,
        "show_opensource_catalogue": (
            settings_svc.get("show_opensource_catalogue") or "true"
        ).lower()
        == "true",
        "active_download_client": settings_svc.get("active_download_client")
        or "sabnzbd",
        "qbittorrent_url": qbt_url,
        "qbittorrent_username": settings_svc.get("qbittorrent_username") or "",
        "qbittorrent_category": settings_svc.get("qbittorrent_category") or "software",
        "qbittorrent_configured": qbt_configured,
        "torznab_adapter_enabled": torznab_enabled,
        # AI assistant
        "ai_enabled": (settings_svc.get("ai_enabled") or "false").lower() == "true",
        "ai_provider": settings_svc.get("ai_provider") or "openai",
        "ai_model": settings_svc.get("ai_model") or "gpt-4o-mini",
        "ai_base_url": settings_svc.get("ai_base_url") or "https://api.openai.com/v1",
        "ai_rate_limit_per_hour": settings_svc.get("ai_rate_limit_per_hour") or "20",
        # Staging queue auto-cleanup
        "staging_auto_cleanup_days": settings_svc.get("staging_auto_cleanup_days")
        or "0",
        # Push notifications
        "push_notifications_enabled": (
            settings_svc.get("push_notifications_enabled") or "false"
        ).lower()
        == "true",
        "push_vapid_public_key": settings_svc.get("push_vapid_public_key") or "",
        "push_vapid_claims_sub": settings_svc.get("push_vapid_claims_sub") or "",
        # UI preferences
        "default_search_mode": settings_svc.get("default_search_mode") or "standard",
        "quick_approve_mode_enabled": (
            settings_svc.get("quick_approve_mode_enabled") or "false"
        ).lower()
        == "true",
    }
    # Load current user's DB record so 2FA status can be shown
    current_user_db = None
    session = get_session_data(request)
    if session:
        from uuid import UUID as _UUID

        try:
            auth_svc = AuthService(db)
            current_user_db = await auth_svc.get_user_by_id(_UUID(session["uid"]))
        except Exception:
            pass

    return templates.TemplateResponse(
        request,
        "settings.html",
        _template_context(
            request,
            "Settings",
            "settings",
            app_settings=app_settings,
            usenet_enabled=usenet_enabled,
            usenet_indexers=indexers,
            current_user=current_user_db,
        ),
    )


# ---------------------------------------------------------------------------
# Health / readiness endpoints (no auth required)
# ---------------------------------------------------------------------------


@app.get("/health", include_in_schema=False)
async def health_check():
    """Liveness probe -- returns 200 if the process is up."""
    return {"status": "ok", "version": APP_VERSION}


@app.get("/ready", include_in_schema=False)
async def readiness_check(db=Depends(get_db)):
    """Readiness probe -- checks DB connectivity. Returns 503 if unhealthy."""
    from fastapi.responses import JSONResponse

    errors = []
    try:
        from sqlalchemy import text

        await db.execute(text("SELECT 1"))
    except Exception as exc:
        errors.append(f"database: {exc}")

    if errors:
        return JSONResponse(
            status_code=503,
            content={"status": "unavailable", "errors": errors},
        )
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# System health page
# ---------------------------------------------------------------------------


@app.get("/system/health")
async def system_health_page(request: Request):
    return templates.TemplateResponse(
        request,
        "system_health.html",
        _template_context(request, "System Health", "system_health"),
    )


# ---------------------------------------------------------------------------
# About page
# ---------------------------------------------------------------------------


def _parse_changelog(text: str) -> list[dict]:
    """Parse a Keep-a-Changelog formatted CHANGELOG.md into structured entries.

    Returns a list of dicts, each with:
      - version: str (e.g. "1.0.0")
      - date: str (e.g. "2026-04-10") or ""
      - sections: list of {"heading": str, "items": [str]}
    """
    import re

    entries = []
    # Split on version headings: ## [x.y.z] - YYYY-MM-DD or ## [Unreleased]
    version_pattern = re.compile(r"^## \[(.+?)\](?:\s*-\s*(.+))?$", re.MULTILINE)
    # Split text into blocks between version headings
    parts = version_pattern.split(text)
    # parts layout after split: [preamble, ver, date, body, ver, date, body, ...]
    # index 0: preamble, then groups of 3: version, date (or None), body
    i = 1
    while i < len(parts):
        version = parts[i].strip()
        date = (parts[i + 1] or "").strip() if i + 1 < len(parts) else ""
        body = parts[i + 2] if i + 2 < len(parts) else ""
        i += 3

        sections = []
        current_heading = None
        current_items: list[str] = []
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("### "):
                if current_heading is not None:
                    sections.append(
                        {"heading": current_heading, "items": current_items}
                    )
                current_heading = stripped[4:].strip()
                current_items = []
            elif stripped.startswith("- "):
                current_items.append(stripped[2:].strip())
        if current_heading is not None and current_items:
            sections.append({"heading": current_heading, "items": current_items})

        entries.append({"version": version, "date": date, "sections": sections})

    return entries


@app.get("/about")
async def about_page(
    request: Request, ini: IniSettingsManager = Depends(get_ini_settings)
):
    """About page -- changelog, contributors, and legal disclaimer."""
    changelog_entries: list[dict] = []
    changelog_path = Path(__file__).parent.parent.parent / "CHANGELOG.md"
    try:
        changelog_text = changelog_path.read_text(encoding="utf-8")
        changelog_entries = _parse_changelog(changelog_text)
    except Exception:
        pass

    repo = "arrsome/softarr"
    token = settings.GITHUB_TOKEN or ""
    repo_root = str(Path(__file__).parent.parent.parent)
    contributor_svc = ContributorService(repo=repo, token=token, repo_path=repo_root)
    contributors = await contributor_svc.get_contributors()

    return templates.TemplateResponse(
        request,
        "about.html",
        _template_context(
            request,
            "About",
            "about",
            changelog_entries=changelog_entries,
            contributors=contributors,
        ),
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("softarr.main:app", host="127.0.0.1", port=8000, reload=settings.DEBUG)
