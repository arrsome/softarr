"""Anti-piracy content filter.

Detects known piracy-related keywords in release names and asset filenames.
When enabled via ``antipiracy_enabled = true`` in softarr.ini, any match
results in a BLOCKED flag with a clear reason message.

This feature is strictly about protecting the user from accidentally
acquiring pirated or tampered software -- it does not apply any judgement
about legal grey areas and is conservative in its matching.
"""

import re
from typing import List

# Keyword patterns. Each entry is a case-insensitive regex fragment.
# Deliberately conservative -- only unambiguous piracy signals.
_PIRACY_PATTERNS: List[str] = [
    r"\bcracked?\b",
    r"\bkeygen\b",
    r"\bkey[_\s-]?gen(erator)?\b",
    r"\bserial[_\s-]?key\b",
    r"\blicense[_\s-]?bypass\b",
    r"\bactivation[_\s-]?crack\b",
    r"\bpatch(er)?\b",
    r"\bwarez\b",
    r"\bpirated?\b",
    r"\bnulled\b",
    r"\bunlocker\b",
    r"\billegal[_\s-]?copy\b",
    r"\bhacked[_\s-]?version\b",
    r"\bdrm[_\s-]?remov(er|al)\b",
    r"\bserial[_\s-]?number[_\s-]?list\b",
    r"\bactivation[_\s-]?script\b",
]

_COMPILED = [re.compile(p, re.IGNORECASE) for p in _PIRACY_PATTERNS]


def scan_for_piracy(text: str) -> List[str]:
    """Return a list of matched piracy keywords found in ``text``.

    Returns an empty list if no matches are found.
    """
    matches = []
    for pattern in _COMPILED:
        m = pattern.search(text)
        if m:
            matches.append(m.group(0))
    return matches


def check_release_for_piracy(name: str, asset_names: List[str]) -> List[str]:
    """Check a release name and its asset filenames for piracy signals.

    Returns a deduplicated list of matched piracy keyword strings.
    """
    found = set()
    for text in [name] + asset_names:
        found.update(scan_for_piracy(text))
    return sorted(found)
