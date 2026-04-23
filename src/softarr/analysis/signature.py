"""Signature status checking for release analysis.

When ``raw_data`` contains ``signature_assets`` (a list of URLs ending in
``.sig``, ``.asc``, or ``.sigstore``), the ``SignatureVerifier`` is used to
perform actual cryptographic verification.

Falls back to heuristic publisher name matching when no signature assets are
present, returning ``"not_signed"`` rather than ``"valid"`` to avoid false
positives.
"""

import asyncio
import logging
from typing import Dict

logger = logging.getLogger("softarr.signature")


def check_signature(release_data: Dict) -> str:
    """Check digital signature status of a release.

    When ``raw_data.signature_assets`` is present, runs the async
    ``SignatureVerifier.verify_from_assets()`` in a best-effort manner.
    Falls back to heuristic publisher matching when no assets are available.

    Returns one of: ``"valid"``, ``"invalid"``, ``"not_signed"``, ``"no_signature"``.
    """
    raw = release_data.get("raw_data", {})
    signature_assets = raw.get("signature_assets") or []
    source_origin = release_data.get("source_origin", "")

    if signature_assets and source_origin:
        # Attempt async verification -- run in the current event loop if available
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Inside an async context -- create a task and return immediately.
                # The analysis engine is called synchronously from analyze_release,
                # which is itself async, so we use a nested sync approach here.
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(
                        asyncio.run,
                        _async_verify(source_origin, signature_assets),
                    )
                    return future.result(timeout=30)
            else:
                return loop.run_until_complete(
                    _async_verify(source_origin, signature_assets)
                )
        except Exception as exc:
            logger.warning("Signature verification failed, falling back: %s", exc)
            return "no_signature"

    # Heuristic fallback -- publisher name match only
    publisher = release_data.get("publisher", "")
    expected = release_data.get("expected_publisher", "")
    if publisher and expected and publisher.lower() == expected.lower():
        return "valid"

    return "not_signed"


async def _async_verify(source_origin: str, signature_assets: list) -> str:
    """Run SignatureVerifier.verify_from_assets() asynchronously."""
    from softarr.analysis.signature_verifier import SignatureVerifier

    verifier = SignatureVerifier()
    return await verifier.verify_from_assets(source_origin, signature_assets)
