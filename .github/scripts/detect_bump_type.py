#!/usr/bin/env python3
"""
Detect the appropriate semantic version bump type from commit subjects.

Usage:
    git log <last_tag>..HEAD --pretty=format:"%s" | python detect_bump_type.py

Prints one of: major, minor, patch

Rules (first match across all subjects wins):
  major -- any subject contains "BREAKING CHANGE" or starts with "major:"
  minor -- any subject matches a feature keyword (add, implement, new feature, etc.)
  patch -- everything else (fix, refactor, update, chore, etc.)
"""

import re
import sys

MAJOR_RE = re.compile(r"(BREAKING[\s_-]CHANGE|^major\s*:)", re.IGNORECASE)
MINOR_RE = re.compile(
    r"(\badd\b|\bimplement|\bnew\b|\bfeature|\bintroduce|\bsupport\b|\benable\b|\bcreate\b)",
    re.IGNORECASE,
)

SKIP_RE = re.compile(
    r"^(bump version|update changelog|merge\b|chore\b|wip\b)",
    re.IGNORECASE,
)


def detect(subjects: list[str]) -> str:
    has_minor = False
    for s in subjects:
        s = s.strip()
        if not s or SKIP_RE.match(s):
            continue
        if MAJOR_RE.search(s):
            return "major"
        if MINOR_RE.search(s):
            has_minor = True
    return "minor" if has_minor else "patch"


if __name__ == "__main__":
    subjects = sys.stdin.read().splitlines()
    print(detect(subjects))
