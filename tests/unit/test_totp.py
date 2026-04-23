"""Tests for TOTP 2FA helper functions (app/auth/totp.py)."""

from softarr.auth.totp import (
    decrypt_secret,
    encrypt_secret,
    generate_qr_png_b64,
    generate_totp_secret,
    get_totp_uri,
    verify_totp_code,
)


class TestGenerateTotpSecret:
    def test_returns_string(self):
        secret = generate_totp_secret()
        assert isinstance(secret, str)

    def test_is_base32(self):
        """Generated secret should only contain base32 characters."""
        import re

        secret = generate_totp_secret()
        assert re.match(r"^[A-Z2-7]+=*$", secret), f"Not base32: {secret!r}"

    def test_minimum_length(self):
        """Secret should be at least 32 chars (pyotp default is 32)."""
        secret = generate_totp_secret()
        assert len(secret) >= 32

    def test_uniqueness(self):
        """Two consecutive calls should produce different secrets."""
        assert generate_totp_secret() != generate_totp_secret()


class TestEncryptDecryptSecret:
    def test_round_trip(self):
        raw = generate_totp_secret()
        stored = encrypt_secret(raw)
        assert decrypt_secret(stored) == raw

    def test_tampered_returns_none(self):
        raw = generate_totp_secret()
        stored = encrypt_secret(raw)
        # Corrupt a byte in the middle of the token
        chars = list(stored)
        chars[len(chars) // 2] = "X" if chars[len(chars) // 2] != "X" else "Y"
        corrupted = "".join(chars)
        assert decrypt_secret(corrupted) is None

    def test_stored_differs_from_raw(self):
        raw = generate_totp_secret()
        stored = encrypt_secret(raw)
        assert stored != raw

    def test_empty_secret_survives_round_trip(self):
        """Edge case: empty string should still round-trip cleanly."""
        stored = encrypt_secret("")
        assert decrypt_secret(stored) == ""


class TestVerifyTotpCode:
    def test_valid_current_code(self):
        """A freshly generated code should verify successfully."""
        import pyotp

        raw = generate_totp_secret()
        stored = encrypt_secret(raw)
        totp = pyotp.TOTP(raw)
        code = totp.now()
        assert verify_totp_code(stored, code) is True

    def test_code_from_one_interval_ago_passes(self):
        """A code generated 30 seconds ago should still verify (drift window = ±60 s)."""
        import time

        import pyotp

        raw = generate_totp_secret()
        stored = encrypt_secret(raw)
        totp = pyotp.TOTP(raw)
        # Generate a code valid for the previous 30-second window.
        code = totp.at(for_time=time.time() - 30)
        assert verify_totp_code(stored, code) is True

    def test_wrong_code_fails(self):
        raw = generate_totp_secret()
        stored = encrypt_secret(raw)
        assert verify_totp_code(stored, "000000") is False

    def test_bad_stored_secret_fails(self):
        """If stored secret is tampered, verification must fail gracefully."""
        assert verify_totp_code("not-a-valid-token", "123456") is False

    def test_empty_code_fails(self):
        raw = generate_totp_secret()
        stored = encrypt_secret(raw)
        assert verify_totp_code(stored, "") is False


class TestGetTotpUri:
    def test_contains_otpauth_scheme(self):
        raw = generate_totp_secret()
        uri = get_totp_uri(raw, "alice", issuer="TestApp")
        assert uri.startswith("otpauth://totp/")

    def test_contains_username(self):
        raw = generate_totp_secret()
        uri = get_totp_uri(raw, "alice")
        assert "alice" in uri

    def test_contains_issuer(self):
        raw = generate_totp_secret()
        uri = get_totp_uri(raw, "alice", issuer="MySoftarr")
        assert "MySoftarr" in uri


class TestGenerateQrPngB64:
    def test_returns_base64_string(self):
        import base64

        raw = generate_totp_secret()
        b64 = generate_qr_png_b64(raw, "alice")
        # Should be decodable base64
        decoded = base64.b64decode(b64)
        # PNG magic bytes
        assert decoded[:4] == b"\x89PNG"
