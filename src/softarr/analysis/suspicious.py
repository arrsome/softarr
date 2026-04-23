import re
from typing import List

SUSPICIOUS_PATTERNS = [
    r"crack",
    r"keygen",
    r"patch(?!notes)",
    r"activator",
    r"loader",
    r"repack",
    r"warez",
    r"nulled",
    r"pirat",
    r"hack(?!athon)",
    r"serial[\s_-]?key",
    r"license[\s_-]?gen",
    r"pre[\s_-]?activated",
]

COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in SUSPICIOUS_PATTERNS]


def detect_suspicious_patterns(filename: str) -> List[str]:
    """Detect suspicious naming patterns in a filename.

    Returns a list of matched pattern strings. These are surfaced to the
    user for review but do not automatically block the release.
    """
    found = []
    for pattern, compiled in zip(SUSPICIOUS_PATTERNS, COMPILED_PATTERNS):
        if compiled.search(filename):
            found.append(pattern)
    return found


def detect_suspicious_in_list(filenames: List[str]) -> List[str]:
    """Run suspicious pattern detection across a list of filenames."""
    all_found = []
    for name in filenames:
        all_found.extend(detect_suspicious_patterns(name))
    return list(set(all_found))
