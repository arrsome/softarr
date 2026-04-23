"""FastAPI dependencies for authentication and authorisation.

Two authentication paths are supported:
  1. Session cookie (browser users) -- validated via itsdangerous signed cookie.
  2. X-Api-Key header (machine-to-machine) -- static key stored in softarr.ini.

Both paths return a user dict with the same shape so route handlers are
agnostic to which path was used.

Usage in routes:
    @router.post("/something", dependencies=[Depends(require_admin)])
    async def do_something(...):
        ...

Or to get the current user:
    @router.get("/me")
    async def me(user: dict = Depends(require_auth)):
        return user
"""

import secrets
from typing import Optional

from fastapi import HTTPException, Request
from starlette.status import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN

from softarr.auth.sessions import get_session_data

# Paths that are always exempt from the force-password-change redirect
_FPC_EXEMPT_PREFIXES = (
    "/auth/",
    "/api/",
    "/static/",
    "/docs",
    "/openapi",
    "/redoc",
    "/change-password",
)

# Synthetic user dict returned for API key authenticated requests
_API_KEY_USER = {"uid": "api", "u": "api-key", "role": "admin", "fpc": False}


def _check_api_key(request: Request) -> Optional[dict]:
    """Return the API key user dict if a valid X-Api-Key header is present."""
    provided = request.headers.get("x-api-key", "")
    if not provided:
        return None
    try:
        from softarr.core.ini_settings import get_ini_settings

        ini = get_ini_settings()
        stored = ini.get("api_key") or ""
    except Exception:
        return None
    if not stored:
        return None
    if secrets.compare_digest(provided, stored):
        return _API_KEY_USER
    return None


async def get_current_user(request: Request) -> Optional[dict]:
    """Return the current session user or None if not logged in."""
    return get_session_data(request)


async def require_auth(request: Request) -> dict:
    """Dependency that requires a valid session or X-Api-Key header.

    Returns user data dict on success.
    If the session has force_password_change set, API requests receive a 403.
    """
    # Try API key first (stateless, fast)
    api_user = _check_api_key(request)
    if api_user:
        return api_user

    user = get_session_data(request)
    if not user:
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    # Force-password-change gate
    if user.get("fpc"):
        path = request.url.path
        if not any(path.startswith(p) for p in _FPC_EXEMPT_PREFIXES):
            if path.startswith("/api/"):
                raise HTTPException(
                    status_code=HTTP_403_FORBIDDEN,
                    detail="Password change required before continuing.",
                )
            # For browser requests return a redirect
            raise HTTPException(
                status_code=302,
                headers={"Location": "/change-password"},
                detail="Password change required.",
            )

    return user


async def require_viewer(request: Request) -> dict:
    """Dependency that requires at least viewer access (any authenticated user)."""
    return await require_auth(request)


async def require_admin(request: Request) -> dict:
    """Dependency that requires an authenticated admin user."""
    user = await require_auth(request)
    role = user.get("role", "admin")  # Legacy sessions without role default to admin
    if role != "admin":
        raise HTTPException(
            status_code=HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    return user
