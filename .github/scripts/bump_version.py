#!/usr/bin/env python3
"""
Bump the Softarr application version across all locations.

Usage:
    python .github/scripts/bump_version.py <patch|minor|major>

Updates:
    - pyproject.toml                  (source of truth)
    - README.md                       (header line)
    - tests/unit/test_app_version.py  (assertion)

Prints the new version string to stdout so callers can capture it.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def current_version() -> str:
    text = read(ROOT / "pyproject.toml")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not m:
        raise ValueError("Could not find version in pyproject.toml")
    return m.group(1)


def bump(version: str, part: str) -> str:
    major, minor, patch = (int(x) for x in version.split("."))
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"Unknown bump type: {part!r}. Expected patch, minor, or major.")


def update_pyproject(old: str, new: str) -> None:
    path = ROOT / "pyproject.toml"
    # Only replace the [project] version line, not any other occurrence.
    content = read(path)
    content = re.sub(
        r'^(version\s*=\s*)"' + re.escape(old) + '"',
        r'\g<1>"' + new + '"',
        content,
        count=1,
        flags=re.MULTILINE,
    )
    write(path, content)


def update_readme(old: str, new: str) -> None:
    path = ROOT / "README.md"
    write(path, read(path).replace(f"v{old}", f"v{new}", 1))


def update_version_test(old: str, new: str) -> None:
    path = ROOT / "tests" / "unit" / "test_app_version.py"
    content = read(path)
    # Update the assertion value and the method name / docstring.
    content = content.replace(f'"{old}"', f'"{new}"')
    old_safe = old.replace(".", "_")
    new_safe = new.replace(".", "_")
    content = content.replace(
        f"test_version_is_{old_safe}", f"test_version_is_{new_safe}"
    )
    content = re.sub(
        r'"""Unit test: application version must be [^"]+\."""',
        f'"""Unit test: application version must be {new}."""',
        content,
    )
    write(path, content)


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] not in ("patch", "minor", "major"):
        print("Usage: bump_version.py <patch|minor|major>", file=sys.stderr)
        sys.exit(1)

    part = sys.argv[1]
    old = current_version()
    new = bump(old, part)

    update_pyproject(old, new)
    update_readme(old, new)
    update_version_test(old, new)

    print(new)


if __name__ == "__main__":
    main()
