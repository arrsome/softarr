"""Rate limiting setup using slowapi.

Applied globally and can be overridden per-route with @limiter.limit().
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

from softarr.core.config import settings

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[settings.RATE_LIMIT_DEFAULT],
)
