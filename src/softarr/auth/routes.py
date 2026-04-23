"""Auth routes: login, logout, 2FA enrolment, 2FA verification."""

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from softarr.auth.dependencies import require_admin
from softarr.auth.service import AuthService
from softarr.auth.sessions import (
    clear_pending_2fa,
    clear_session,
    get_pending_2fa,
    get_session_data,
    set_pending_2fa,
    set_session,
)
from softarr.core.database import get_db
from softarr.middleware.csrf import get_csrf_token

router = APIRouter()


# ---------------------------------------------------------------------------
# Login / logout
# ---------------------------------------------------------------------------


def _login_error_response(request: Request, error: str, *, status_code: int):
    """Re-render the login page with an error message.

    Used by the POST /login handler so that a failed login shows the form
    again instead of FastAPI's default JSON error body. Late-imports
    ``templates`` and ``APP_VERSION`` from ``softarr.main`` to avoid a
    circular import at module load.
    """
    from softarr.main import APP_VERSION, templates

    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "title": "Login",
            "csrf_token": get_csrf_token(request),
            "app_version": APP_VERSION,
            "error": error,
        },
        status_code=status_code,
    )


@router.post("/login")
async def login(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """Authenticate and set session cookie.

    If the user has TOTP 2FA enabled, a pending-2FA cookie is set and the
    browser is redirected to /auth/2fa/verify instead of the main app.

    On invalid input or bad credentials the login form is re-rendered with
    an error message instead of returning a JSON error -- the form posts
    here directly from the browser, so a JSON body would be shown raw.
    """
    form = await request.form()
    username = form.get("username", "")
    password = form.get("password", "")

    if not username or not password:
        return _login_error_response(
            request,
            "Username and password are required.",
            status_code=400,
        )

    service = AuthService(db)
    user = await service.authenticate(str(username), str(password))
    if not user:
        return _login_error_response(
            request,
            "Invalid username or password.",
            status_code=401,
        )

    role = getattr(user, "role", None) or ("admin" if user.is_admin else "viewer")
    force_pw = bool(user.force_password_change)
    disclaimer_ok = bool(getattr(user, "disclaimer_accepted", False))

    # If the user has not yet accepted the legal disclaimer, redirect them to
    # the disclaimer page. Issue a session cookie with da=False so the auth
    # gate lets them reach /auth/disclaimer.
    if not disclaimer_ok:
        redirect = RedirectResponse(url="/auth/disclaimer", status_code=303)
        set_session(
            redirect,
            user.id,
            user.username,
            is_admin=user.is_admin,
            force_password_change=force_pw,
            role=role,
            disclaimer_accepted=False,
        )
        return redirect

    # If 2FA is enabled, issue a pending cookie and redirect to verify page
    if getattr(user, "totp_enabled", False):
        redirect = RedirectResponse(url="/auth/2fa/verify", status_code=303)
        set_pending_2fa(
            redirect,
            str(user.id),
            user.username,
            role,
            force_pw,
            disclaimer_accepted=True,
        )
        return redirect

    redirect = RedirectResponse(url="/", status_code=303)
    set_session(
        redirect,
        user.id,
        user.username,
        is_admin=user.is_admin,
        force_password_change=force_pw,
        role=role,
        disclaimer_accepted=True,
    )
    return redirect


@router.post("/logout")
async def logout():
    """Clear session and redirect to login."""
    redirect = RedirectResponse(url="/login", status_code=303)
    clear_session(redirect)
    clear_pending_2fa(redirect)
    return redirect


@router.get("/status")
async def auth_status(request: Request):
    """Return current auth status for API consumers."""
    data = get_session_data(request)
    if data:
        return {"authenticated": True, "username": data.get("u")}
    return {"authenticated": False}


# ---------------------------------------------------------------------------
# 2FA verification during login (the second step)
# ---------------------------------------------------------------------------


@router.get("/2fa/verify")
async def totp_verify_page(request: Request):
    """Show the TOTP code entry page."""
    pending = get_pending_2fa(request)
    if not pending:
        return RedirectResponse(url="/login", status_code=303)

    from softarr.main import templates

    return templates.TemplateResponse(
        request,
        "2fa_verify.html",
        {"username": pending.get("u", "")},
    )


@router.post("/2fa/verify")
async def totp_verify_submit(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Validate the submitted TOTP code and complete login."""
    pending = get_pending_2fa(request)
    if not pending:
        return RedirectResponse(url="/login", status_code=303)

    form = await request.form()
    code = str(form.get("code", "")).strip().replace(" ", "")

    from uuid import UUID

    service = AuthService(db)
    user_id = UUID(pending["uid"])
    if not await service.verify_totp(user_id, code):
        from softarr.main import templates

        return templates.TemplateResponse(
            request,
            "2fa_verify.html",
            {
                "username": pending.get("u", ""),
                "error": "Invalid code -- please try again.",
            },
            status_code=401,
        )

    # Code valid -- issue the real session and clear the pending cookie
    user = await service.get_user_by_id(user_id)
    redirect = RedirectResponse(url="/", status_code=303)
    set_session(
        redirect,
        user.id,
        user.username,
        is_admin=user.is_admin,
        force_password_change=bool(user.force_password_change),
        role=pending.get("role", "admin"),
        disclaimer_accepted=bool(pending.get("da", False)),
    )
    clear_pending_2fa(redirect)
    return redirect


# ---------------------------------------------------------------------------
# 2FA setup (enrolment) -- requires full session (already logged in)
# ---------------------------------------------------------------------------


@router.get("/2fa/setup")
async def totp_setup_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Show QR code and manual entry key for enrolling a TOTP authenticator app."""
    from uuid import UUID

    from softarr.auth.totp import generate_qr_png_b64
    from softarr.core.ini_settings import get_ini_settings

    service = AuthService(db)
    user_id = UUID(user["uid"])
    db_user = await service.get_user_by_id(user_id)

    ini = get_ini_settings()
    issuer = ini.get("totp_issuer") or "Softarr"

    # If already has a pending (not-yet-confirmed) secret, reuse it;
    # otherwise generate a fresh one.
    if db_user.totp_secret and not db_user.totp_enabled:
        from softarr.auth.totp import decrypt_secret

        raw_secret = decrypt_secret(db_user.totp_secret)
        if not raw_secret:
            raw_secret = await service.enable_totp(user_id)
    else:
        raw_secret = await service.enable_totp(user_id)

    qr_b64 = generate_qr_png_b64(raw_secret, db_user.username, issuer)
    manual_key = raw_secret  # shown to user for manual entry

    from softarr.main import _template_context, templates

    return templates.TemplateResponse(
        request,
        "2fa_setup.html",
        _template_context(
            request,
            title="Two-Factor Authentication Setup",
            active_page="settings",
            qr_b64=qr_b64,
            manual_key=manual_key,
            totp_enabled=db_user.totp_enabled,
        ),
    )


@router.post("/2fa/setup")
async def totp_setup_confirm(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Confirm enrolment by validating the first TOTP code."""
    from uuid import UUID

    form = await request.form()
    code = str(form.get("code", "")).strip().replace(" ", "")

    service = AuthService(db)
    user_id = UUID(user["uid"])

    if not await service.confirm_totp_enrolment(user_id, code):
        from softarr.auth.totp import decrypt_secret, generate_qr_png_b64
        from softarr.core.ini_settings import get_ini_settings

        db_user = await service.get_user_by_id(user_id)
        ini = get_ini_settings()
        issuer = ini.get("totp_issuer") or "Softarr"
        raw_secret = decrypt_secret(db_user.totp_secret) if db_user.totp_secret else ""
        qr_b64 = (
            generate_qr_png_b64(raw_secret, db_user.username, issuer)
            if raw_secret
            else ""
        )
        from softarr.main import _template_context, templates

        return templates.TemplateResponse(
            request,
            "2fa_setup.html",
            _template_context(
                request,
                title="Two-Factor Authentication Setup",
                active_page="settings",
                qr_b64=qr_b64,
                manual_key=raw_secret,
                totp_enabled=False,
                error="Invalid code -- please scan the QR code again and try a fresh code.",
            ),
            status_code=400,
        )

    return RedirectResponse(url="/auth/2fa/setup?enabled=1", status_code=303)


@router.post("/2fa/disable")
async def totp_disable(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Disable 2FA for the current user."""
    from uuid import UUID

    service = AuthService(db)
    await service.disable_totp(UUID(user["uid"]))
    return RedirectResponse(url="/auth/2fa/setup?disabled=1", status_code=303)


# ---------------------------------------------------------------------------
# Legal disclaimer acceptance
# ---------------------------------------------------------------------------


@router.get("/disclaimer")
async def disclaimer_page(
    request: Request,
    user: dict = Depends(require_admin),
):
    """Show the legal disclaimer page.

    Requires a valid session (the user must have authenticated with their
    password). The disclaimer must be accepted before the user can access
    any other part of the application.
    """
    from softarr.main import templates
    from softarr.middleware.csrf import get_csrf_token

    return templates.TemplateResponse(
        request,
        "disclaimer.html",
        {"csrf_token": get_csrf_token(request), "app_version": None},
    )


@router.post("/disclaimer/accept")
async def disclaimer_accept(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(require_admin),
):
    """Record disclaimer acceptance and redirect to 2FA setup.

    Server-side enforcement: the disclaimer_accepted flag is set in the
    database and the session cookie is regenerated with da=True. Without
    this, the auth gate will continue to redirect to this page.
    """
    from uuid import UUID

    from softarr.auth.sessions import set_session

    service = AuthService(db)
    user_id = UUID(user["uid"])
    await service.accept_disclaimer(user_id)

    db_user = await service.get_user_by_id(user_id)

    # Regenerate session cookie with da=True so the auth gate allows through
    redirect = RedirectResponse(url="/auth/2fa/setup", status_code=303)
    set_session(
        redirect,
        db_user.id,
        db_user.username,
        is_admin=db_user.is_admin,
        force_password_change=bool(db_user.force_password_change),
        role=user.get("role", "admin"),
        disclaimer_accepted=True,
    )
    return redirect
