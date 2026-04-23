"""Release rules engine: version pinning, auto-reject, and release type filtering.

These rules are evaluated during release discovery (scheduler and manual search)
to prevent unwanted releases from entering the workflow.

Version pinning
---------------
``software.version_pin`` is a dict or None:
  ``{"mode": "exact", "value": "1.2.3"}``   -- only 1.2.3 is accepted
  ``{"mode": "major", "value": "1"}``        -- only 1.x.x releases accepted
  ``{"mode": "disabled"}``                   -- pinning turned off (same as None)

Auto-reject rules
-----------------
``software.auto_reject_rules`` is a list of rule name strings. Supported rules:
  "pre_release"    -- reject alpha, beta, rc, preview versions
  "nightly"        -- reject nightly / dev / snapshot builds
  "portable"       -- reject portable distributions
  "unsigned"       -- reject unsigned assets (only when signature check has run)
  "wrong_publisher" -- reject when publisher does not match expected_publisher

Release type filtering
----------------------
``software.release_type_filter`` is a list of allowed type strings. When
non-empty, only releases whose detected type is in the list are accepted.
Supported types: "installer", "archive", "source", "binary"
An empty list means all types are allowed.
"""

import re
from typing import List, Optional

_PRE_RELEASE_RE = re.compile(
    r"[\-\._]?(?:alpha|beta|rc|preview|pre[\-\._]?release)[\d.]*",
    re.IGNORECASE,
)
_NIGHTLY_RE = re.compile(
    r"[\-\._]?(?:nightly|dev|snapshot|unstable|trunk)[\d.]*",
    re.IGNORECASE,
)
_PORTABLE_RE = re.compile(r"[\-\._]?portable\b", re.IGNORECASE)

# Extension-based release type detection
_INSTALLER_EXTS = {".exe", ".msi", ".pkg", ".dmg", ".deb", ".rpm", ".apk"}
_ARCHIVE_EXTS = {".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar", ".tgz"}
_SOURCE_EXTS = {".tar.gz", ".tar.bz2", ".tar.xz", ".zip"}  # overlap is intentional
_SOURCE_KEYWORDS = re.compile(r"\bsource\b|\bsrc\b", re.IGNORECASE)
_BINARY_KEYWORDS = re.compile(r"\bbin(ary)?\b", re.IGNORECASE)


def _detect_release_type(version: str, name: str, asset_names: List[str]) -> str:
    """Return the most likely release type string for a release.

    Priority: installer > source > binary > archive > unknown
    """
    combined = " ".join([name, version] + asset_names).lower()

    for ext in _INSTALLER_EXTS:
        if ext in combined:
            return "installer"

    if _SOURCE_KEYWORDS.search(combined):
        return "source"

    if _BINARY_KEYWORDS.search(combined):
        return "binary"

    for ext in _ARCHIVE_EXTS:
        if ext in combined:
            return "archive"

    return "unknown"


def check_version_pin(
    version: str,
    version_pin: Optional[dict],
) -> tuple[bool, str]:
    """Check whether a version satisfies the version pin.

    Returns ``(is_allowed, reason_if_rejected)``.
    """
    if not version_pin:
        return True, ""

    mode = (version_pin.get("mode") or "disabled").lower()
    if mode == "disabled":
        return True, ""

    pin_value = str(version_pin.get("value") or "").strip()
    if not pin_value:
        return True, ""

    if mode == "exact":
        if version == pin_value:
            return True, ""
        return (
            False,
            f"Version pin (exact): only {pin_value!r} is accepted, got {version!r}",
        )

    if mode == "major":
        # Accept versions starting with the pinned major component
        candidate_major = version.split(".")[0] if version else ""
        if candidate_major == pin_value:
            return True, ""
        return (
            False,
            f"Version pin (major): only {pin_value}.x.x is accepted, got {version!r}",
        )

    return True, ""


def check_auto_reject_rules(
    version: str,
    name: str,
    asset_names: List[str],
    rules: List[str],
    publisher: Optional[str] = None,
    expected_publisher: Optional[str] = None,
    signature_status: Optional[str] = None,
) -> tuple[bool, str]:
    """Check whether a release should be auto-rejected based on configured rules.

    Returns ``(should_reject, reason)``.
    """
    if not rules:
        return False, ""

    candidate_text = " ".join([version, name] + asset_names)

    if "pre_release" in rules and _PRE_RELEASE_RE.search(candidate_text):
        return (
            True,
            "Auto-reject (pre_release): version/name contains pre-release marker",
        )

    if "nightly" in rules and _NIGHTLY_RE.search(candidate_text):
        return (
            True,
            "Auto-reject (nightly): version/name indicates a nightly/dev build",
        )

    if "portable" in rules and _PORTABLE_RE.search(candidate_text):
        return True, "Auto-reject (portable): portable distribution not allowed"

    if "unsigned" in rules and signature_status == "invalid":
        return True, "Auto-reject (unsigned): release signature is missing or invalid"

    if "wrong_publisher" in rules and publisher and expected_publisher:
        if publisher.lower() != expected_publisher.lower():
            return True, (
                f"Auto-reject (wrong_publisher): expected {expected_publisher!r}, "
                f"got {publisher!r}"
            )

    return False, ""


def check_release_type_filter(
    version: str,
    name: str,
    asset_names: List[str],
    allowed_types: List[str],
) -> tuple[bool, str]:
    """Check whether the detected release type is in the allowed list.

    Returns ``(is_allowed, reason_if_rejected)``.
    An empty ``allowed_types`` list means all types are permitted.
    """
    if not allowed_types:
        return True, ""

    detected = _detect_release_type(version, name, asset_names)
    if detected in allowed_types:
        return True, ""

    return False, (
        f"Release type filter: detected type {detected!r} is not in allowed types "
        f"{allowed_types!r}"
    )
