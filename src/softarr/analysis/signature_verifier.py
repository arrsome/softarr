"""Cryptographic signature verification.

Provides GPG and sigstore-based signature verification for release assets.
Used by the analysis engine when ``raw_data`` contains ``signature_assets``
(URLs ending in ``.sig``, ``.asc``, or ``.sigstore``).

GPG verification requires the ``python-gnupg`` package.
Sigstore verification requires the ``sigstore`` package (optional).

If neither package is available the verifier returns ``"no_signature"`` rather
than raising an exception.
"""

import logging
import tempfile
from typing import Optional

import httpx

logger = logging.getLogger("softarr.signature_verifier")

REQUEST_TIMEOUT = 15
MAX_ASSET_BYTES = 10 * 1024 * 1024  # 10 MB


async def _download_bytes(url: str) -> Optional[bytes]:
    """Download a URL and return its bytes, or None on failure."""
    try:
        async with httpx.AsyncClient(
            timeout=REQUEST_TIMEOUT, follow_redirects=True
        ) as client:
            resp = await client.get(url, headers={"User-Agent": "softarr/1.0"})
        if resp.status_code != 200:
            logger.debug("Signature asset returned HTTP %d: %s", resp.status_code, url)
            return None
        if len(resp.content) > MAX_ASSET_BYTES:
            logger.warning("Signature asset too large: %s", url)
            return None
        return resp.content
    except httpx.RequestError as exc:
        logger.debug("Failed to download signature asset %s: %s", url, exc)
        return None


class SignatureVerifier:
    """Verify release signatures using GPG or sigstore."""

    async def verify_gpg(
        self,
        content: bytes,
        signature: bytes,
        key_id: Optional[str] = None,
    ) -> str:
        """Verify a GPG/PGP detached signature.

        Returns one of:
          - ``"valid"``: signature verified successfully
          - ``"invalid"``: signature present but verification failed
          - ``"no_signature"``: signature bytes are empty
          - ``"key_not_found"``: public key not in keyring
          - ``"error"``: unexpected error (python-gnupg not installed, etc.)
        """
        if not signature:
            return "no_signature"

        try:
            import gnupg  # type: ignore[import]
        except ImportError:
            logger.debug("python-gnupg not installed -- GPG verification unavailable")
            return "error"

        try:
            with tempfile.TemporaryDirectory() as gpg_home:
                gpg = gnupg.GPG(gnupghome=gpg_home)
                with tempfile.NamedTemporaryFile(
                    suffix=".sig", delete=False
                ) as sig_file:
                    sig_file.write(signature)
                    sig_path = sig_file.name

                verified = gpg.verify_data(sig_path, content)
                if verified.valid:
                    return "valid"
                if "No public key" in (verified.stderr or ""):
                    return "key_not_found"
                return "invalid"
        except Exception as exc:
            logger.warning("GPG verification error: %s", exc)
            return "error"

    async def verify_sigstore(
        self,
        artifact_url: str,
        bundle_url: str,
        identity: Optional[str] = None,
    ) -> str:
        """Verify a sigstore bundle for the given artifact URL.

        Returns one of: ``"valid"``, ``"invalid"``, ``"no_signature"``, ``"error"``.
        """
        try:
            import sigstore  # type: ignore[import]  # noqa: F401
        except ImportError:
            logger.debug("sigstore not installed -- sigstore verification unavailable")
            return "error"

        artifact_bytes = await _download_bytes(artifact_url)
        bundle_bytes = await _download_bytes(bundle_url)

        if not bundle_bytes:
            return "no_signature"
        if not artifact_bytes:
            return "error"

        try:
            from sigstore.models import Bundle  # type: ignore[import]
            from sigstore.verify import Verifier  # type: ignore[import]

            bundle = Bundle.from_json(bundle_bytes.decode())
            verifier = Verifier.production()
            verifier.verify_artifact(artifact_bytes, bundle)
            return "valid"
        except Exception as exc:
            logger.warning("Sigstore verification failed: %s", exc)
            return "invalid"

    async def verify_from_assets(
        self,
        artifact_url: str,
        signature_assets: list[str],
    ) -> str:
        """Attempt verification using any available signature asset URLs.

        Iterates over ``signature_assets`` in order, trying GPG for ``.sig``/``.asc``
        files and sigstore for ``.sigstore``/``.bundle`` files.

        Returns the first non-``"error"`` result, or ``"no_signature"`` if none
        of the assets could be used.
        """
        if not signature_assets or not artifact_url:
            return "no_signature"

        artifact_bytes = await _download_bytes(artifact_url)
        if not artifact_bytes:
            return "no_signature"

        for asset_url in signature_assets:
            lower = asset_url.lower()
            if lower.endswith(".sigstore") or lower.endswith(".bundle"):
                result = await self.verify_sigstore(artifact_url, asset_url)
                if result != "error":
                    return result
            elif lower.endswith(".sig") or lower.endswith(".asc"):
                sig_bytes = await _download_bytes(asset_url)
                if sig_bytes:
                    result = await self.verify_gpg(artifact_bytes, sig_bytes)
                    if result != "error":
                        return result

        return "no_signature"
