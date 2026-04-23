"""TOTP (Time-based One-Time Password) helpers for 2FA enrolment and verification.

Uses pyotp (RFC 6238). Secrets are stored in the DB as itsdangerous-signed
strings so tampering is detectable. This is NOT AES encryption -- a compromised
DB can still be attacked by brute-force of the HMAC key, so setting a strong
SECRET_KEY in .env is important.

Post-1.0: replace with Fernet symmetric encryption for true at-rest secrecy.
"""

import base64
import io
import logging

import pyotp
import qrcode
from itsdangerous import BadSignature, URLSafeSerializer

from softarr.core.config import settings

logger = logging.getLogger("softarr.auth.totp")

_signer = URLSafeSerializer(settings.SECRET_KEY, salt="totp-secret")

TOTP_ISSUER_DEFAULT = "Softarr"
TOTP_DIGITS = 6
TOTP_INTERVAL = 30
TOTP_VALID_WINDOW = 1  # allow ±1 interval (±30 s) for clock drift


def generate_totp_secret() -> str:
    """Generate a new random base32 TOTP secret."""
    return pyotp.random_base32()


def encrypt_secret(raw_secret: str) -> str:
    """Sign the TOTP secret for tamper-evident storage.

    The returned value is safe to store in the DB.
    """
    return _signer.dumps(raw_secret)


def decrypt_secret(stored: str) -> str | None:
    """Recover the raw TOTP secret from its signed form.

    Returns None if the value has been tampered with or is from a different
    SECRET_KEY.
    """
    try:
        return _signer.loads(stored)
    except BadSignature:
        logger.warning(
            "TOTP secret signature verification failed -- possible tampering"
        )
        return None


def verify_totp_code(stored_secret: str, code: str) -> bool:
    """Verify a TOTP code against the stored (signed) secret.

    Returns True if the code is valid within the allowed clock-drift window.
    """
    raw = decrypt_secret(stored_secret)
    if not raw:
        return False
    totp = pyotp.TOTP(raw, digits=TOTP_DIGITS, interval=TOTP_INTERVAL)
    return totp.verify(code, valid_window=TOTP_VALID_WINDOW)


def get_totp_uri(
    raw_secret: str, username: str, issuer: str = TOTP_ISSUER_DEFAULT
) -> str:
    """Return the otpauth:// URI for QR code generation."""
    totp = pyotp.TOTP(raw_secret, digits=TOTP_DIGITS, interval=TOTP_INTERVAL)
    return totp.provisioning_uri(name=username, issuer_name=issuer)


def generate_qr_png_b64(
    raw_secret: str, username: str, issuer: str = TOTP_ISSUER_DEFAULT
) -> str:
    """Return a base64-encoded PNG QR code for the TOTP provisioning URI.

    The returned string can be embedded directly in an <img src="data:image/png;base64,...">
    tag without a round-trip to a QR code API endpoint.
    """
    uri = get_totp_uri(raw_secret, username, issuer)
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return base64.b64encode(buf.read()).decode("ascii")
