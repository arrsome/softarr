#!/usr/bin/env python3
"""
Prepend a new version block to CHANGELOG.md.

Usage:
    python .github/scripts/update_changelog.py <version> <bump_type> <commit_log>

Arguments:
    version     -- New version string, e.g. "1.2.0"
    bump_type   -- patch | minor | major
    commit_log  -- Raw newline-separated git log (subject lines), passed via stdin
                   or as a third CLI argument (quoted, newline-separated).

The script categorises each commit subject into Added / Fixed / Changed / Removed
using simple keyword matching, then inserts the block after the CHANGELOG header
and before the first existing ## version entry.
"""

import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
CHANGELOG = ROOT / "CHANGELOG.md"

# Keywords that signal each category (checked case-insensitively against subject)
CATEGORY_RULES = [
    ("Removed", [r"\bremov", r"\bdrop\b", r"\bdelete\b", r"\bdeprecate"]),
    ("Fixed",   [r"\bfix", r"\bbug\b", r"\brepair", r"\bcorrect", r"\bresolve", r"\bpatch\b"]),
    ("Changed", [r"\brefactor", r"\bupdate\b", r"\bimprove", r"\bchange\b", r"\bmodify", r"\bbump\b", r"\bredesign", r"\bmove\b", r"\bmigrat"]),
    ("Added",   [r"\badd\b", r"\bnew\b", r"\bimplement", r"\bintroduce", r"\bfeature", r"\bsupport\b", r"\benable\b", r"\bcreate\b"]),
]

SKIP_PATTERNS = [
    r"^merge\b",
    r"^bump version",
    r"^update changelog",
    r"^chore\b",
]


def categorise(subject: str) -> str:
    low = subject.lower()
    for pattern in SKIP_PATTERNS:
        if re.search(pattern, low):
            return ""
    for category, patterns in CATEGORY_RULES:
        for p in patterns:
            if re.search(p, low):
                return category
    return "Changed"


def build_block(version: str, subjects: list[str]) -> str:
    today = date.today().strftime("%Y-%m-%d")
    buckets: dict[str, list[str]] = {"Added": [], "Changed": [], "Fixed": [], "Removed": []}

    for subject in subjects:
        subject = subject.strip()
        if not subject:
            continue
        cat = categorise(subject)
        if cat:
            buckets[cat].append(subject)

    lines = [f"## [{version}] - {today}", ""]
    for cat in ("Added", "Changed", "Fixed", "Removed"):
        items = buckets[cat]
        if items:
            lines.append(f"### {cat}")
            lines.append("")
            for item in items:
                lines.append(f"- {item}")
            lines.append("")

    if all(not v for v in buckets.values()):
        lines.append("### Changed")
        lines.append("")
        lines.append("- Release")
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: update_changelog.py <version> [bump_type]", file=sys.stderr)
        sys.exit(1)

    version = sys.argv[1]
    subjects = sys.stdin.read().splitlines()

    block = build_block(version, subjects)

    content = CHANGELOG.read_text(encoding="utf-8")

    # Insert after the header comment block and before the first ## entry.
    insert_re = re.compile(r"(^## \[)", re.MULTILINE)
    m = insert_re.search(content)
    if m:
        content = content[: m.start()] + block + "\n" + content[m.start() :]
    else:
        content = content.rstrip() + "\n\n" + block

    CHANGELOG.write_text(content, encoding="utf-8")
    print(f"CHANGELOG.md updated for {version}")


if __name__ == "__main__":
    main()
