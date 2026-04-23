"""CSRF protection for state-changing browser requests.

Generates a per-session CSRF token and validates it on POST/PUT/PATCH/DELETE
requests that come from the browser (non-API requests, or API requests
with session cookies).

Templates should include the token in forms:
    <input type="hidden" name="_csrf_token" value="{{ csrf_token }}">

HTMX requests include it via a header set in base.html:
    hx-headers='{"X-CSRF-Token": "{{ csrf_token }}"}'

Validation priority:
  1. X-CSRF-Token header (HTMX and fetch() callers)
  2. _csrf_token field in application/x-www-form-urlencoded or multipart body
  3. Reject with 403 if neither is present and valid
"""

import secrets

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from softarr.core.config import settings

CSRF_COOKIE = "softarr_csrf"
CSRF_HEADER = "x-csrf-token"
CSRF_FIELD = "_csrf_token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


class CSRFMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Skip CSRF for safe methods
        if request.method in SAFE_METHODS:
            response = await call_next(request)
            _ensure_csrf_cookie(request, response)
            return response

        # Skip CSRF for non-browser API calls (no session cookie)
        from softarr.auth.sessions import COOKIE_NAME

        if COOKIE_NAME not in request.cookies:
            return await call_next(request)

        # Validate CSRF token
        expected = request.cookies.get(CSRF_COOKIE)
        if not expected:
            return Response("CSRF token missing", status_code=403)

        # Check header first (HTMX / fetch)
        actual = request.headers.get(CSRF_HEADER)

        # Fall back to form body for plain HTML form submissions
        if not actual:
            content_type = request.headers.get("content-type", "")
            if (
                "application/x-www-form-urlencoded" in content_type
                or "multipart/form-data" in content_type
            ):
                try:
                    form = await request.form()
                    actual = form.get(CSRF_FIELD, "")
                except Exception:
                    actual = ""

        if not actual or not secrets.compare_digest(str(actual), expected):
            return Response("CSRF token invalid", status_code=403)

        response = await call_next(request)
        _ensure_csrf_cookie(request, response)
        return response


def _ensure_csrf_cookie(request: Request, response: Response) -> None:
    """Set the CSRF cookie if not already present."""
    if CSRF_COOKIE not in request.cookies:
        token = generate_csrf_token()
        response.set_cookie(
            key=CSRF_COOKIE,
            value=token,
            httponly=False,  # JS needs to read this for HTMX headers
            samesite="strict",
            secure=not settings.DEBUG,
        )


def get_csrf_token(request: Request) -> str:
    """Get the current CSRF token for template rendering."""
    token = request.cookies.get(CSRF_COOKIE)
    if not token:
        token = generate_csrf_token()
    return token
