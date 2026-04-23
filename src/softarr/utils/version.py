"""Version comparison utilities."""

import re


def _version_tuple(version: str) -> tuple:
    """Convert a version string to a comparable tuple of ints.

    Strips leading 'v', ignores non-numeric segments.
    Returns (0,) for unknown/unparseable versions so they sort lowest.

    Examples:
      '26.2.1'  -> (26, 2, 1)
      'v7.6.1'  -> (7, 6, 1)
      'unknown' -> (0,)
    """
    version = version.strip().lstrip("vV")
    parts = re.split(r"[.\-_]", version)
    result = []
    for part in parts:
        if part.isdigit():
            result.append(int(part))
        else:
            # Stop at first non-numeric segment (e.g. "beta", "rc1")
            break
    return tuple(result) if result else (0,)


def compare_versions(a: str, b: str) -> int:
    """Compare two version strings.

    Returns:
      1  if a > b
      0  if a == b
      -1 if a < b
    """
    ta = _version_tuple(a)
    tb = _version_tuple(b)
    if ta > tb:
        return 1
    if ta < tb:
        return -1
    return 0
