"""Cookie-based session management using itsdangerous.

Sessions are stored as signed cookies. The cookie contains the user ID
and a timestamp. The server validates the signature on each request.
No server-side session store is needed for this simple approach.
"""

from typing import Optional
from uuid import UUID

from fastapi import Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from softarr.core.config import settings

COOKIE_NAME = "softarr_session"
PENDING_2FA_COOKIE = "softarr_pending_2fa"

_serializer = URLSafeTimedSerializer(settings.SECRET_KEY)
# Shorter TTL for the intermediate 2FA-pending cookie (5 minutes)
_pending_2fa_serializer = URLSafeTimedSerializer(
    settings.SECRET_KEY, salt="pending-2fa"
)
_PENDING_2FA_MAX_AGE = 300  # 5 minutes


def create_session_cookie(
    user_id: UUID,
    username: str,
    is_admin: bool = True,
    force_password_change: bool = False,
    role: str | None = None,
    disclaimer_accepted: bool = False,
) -> str:
    """Create a signed session token.

    Includes role (admin/viewer), force_password_change flag, and disclaimer
    acceptance flag (``da``) so that per-request auth checks do not need a
    database lookup.

    The ``role`` parameter takes precedence over the legacy ``is_admin`` flag
    when present. New code should always pass ``role`` directly from the User
    model; ``is_admin`` is retained for backwards compatibility.
    """
    resolved_role = role if role is not None else ("admin" if is_admin else "viewer")
    return _serializer.dumps(
        {
            "uid": str(user_id),
            "u": username,
            "role": resolved_role,
            "fpc": force_password_change,
            "da": disclaimer_accepted,
        }
    )


def read_session_cookie(token: str) -> Optional[dict]:
    """Read and validate a session token. Returns None if invalid or expired."""
    try:
        data = _serializer.loads(token, max_age=settings.SESSION_MAX_AGE_SECONDS)
        return data
    except BadSignature, SignatureExpired:
        return None


def set_session(
    response: Response,
    user_id: UUID,
    username: str,
    is_admin: bool = True,
    force_password_change: bool = False,
    role: str | None = None,
    disclaimer_accepted: bool = False,
) -> None:
    """Set the session cookie on a response."""
    token = create_session_cookie(
        user_id,
        username,
        is_admin=is_admin,
        force_password_change=force_password_change,
        role=role,
        disclaimer_accepted=disclaimer_accepted,
    )
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="strict",
        max_age=settings.SESSION_MAX_AGE_SECONDS,
        secure=not settings.DEBUG,
    )


def clear_session(response: Response) -> None:
    """Remove the session cookie."""
    response.delete_cookie(key=COOKIE_NAME)


def get_session_data(request: Request) -> Optional[dict]:
    """Extract session data from the request cookie."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    return read_session_cookie(token)


# ---------------------------------------------------------------------------
# Pending-2FA cookie -- set after password check, cleared after TOTP verify
# ---------------------------------------------------------------------------


def set_pending_2fa(
    response: Response,
    user_id: str,
    username: str,
    role: str,
    force_password_change: bool,
    disclaimer_accepted: bool = False,
) -> None:
    """Set a short-lived cookie indicating the user has passed password check
    and is waiting for a TOTP code to complete login.

    The ``disclaimer_accepted`` flag is carried through so the final session
    cookie issued after TOTP verification reflects the correct disclaimer state.
    """
    token = _pending_2fa_serializer.dumps(
        {
            "uid": user_id,
            "u": username,
            "role": role,
            "fpc": force_password_change,
            "da": disclaimer_accepted,
        }
    )
    response.set_cookie(
        key=PENDING_2FA_COOKIE,
        value=token,
        httponly=True,
        samesite="strict",
        max_age=_PENDING_2FA_MAX_AGE,
        secure=not settings.DEBUG,
    )


def get_pending_2fa(request: Request) -> Optional[dict]:
    """Read and validate the pending-2FA cookie. Returns None if missing/expired."""
    from itsdangerous import BadSignature, SignatureExpired

    token = request.cookies.get(PENDING_2FA_COOKIE)
    if not token:
        return None
    try:
        return _pending_2fa_serializer.loads(token, max_age=_PENDING_2FA_MAX_AGE)
    except BadSignature, SignatureExpired:
        return None


def clear_pending_2fa(response: Response) -> None:
    """Remove the pending-2FA cookie."""
    response.delete_cookie(key=PENDING_2FA_COOKIE)
