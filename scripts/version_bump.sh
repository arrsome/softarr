#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
PYPROJECT="$REPO_ROOT/pyproject.toml"
README="$REPO_ROOT/README.md"
VERSION_TEST="$REPO_ROOT/tests/unit/test_app_version.py"

BUMP="${1:-patch}"

# Tempdir for pre-edit snapshots, so we can diff each file against its own
# pristine copy afterwards. Using a distinct name to avoid clobbering the
# standard TMPDIR environment variable.
TMPDIR_SNAP="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_SNAP"' EXIT

snapshot_file() {
  # Store a copy of $1 keyed by basename. Assumes basenames are unique
  # across the three target files, which they are.
  cp -- "$1" "$TMPDIR_SNAP/$(basename -- "$1")"
}

show_diff() {
  # Diff the pre-edit snapshot against the current file and print it under
  # a per-file header. `diff` exits 1 when files differ, which is the
  # expected case -- capture the output so `set -e` doesn't abort and we
  # can strip diff's own tempfile `--- / +++` header lines (the first two
  # lines of output) before printing. Exit status >= 2 is a real error.
  local path="$1"
  local snap="$TMPDIR_SNAP/$(basename -- "$path")"
  local rel="${path#"$REPO_ROOT"/}"
  local out status
  set +e
  out="$(diff -U1 -- "$snap" "$path")"
  status=$?
  set -e
  printf '\n--- %s ---\n' "$rel"
  if [[ $status -eq 0 ]]; then
    printf '  (no change)\n'
  elif [[ $status -eq 1 ]]; then
    # Drop diff's own header (first two lines: `--- <snap>` and `+++ <path>`)
    # since we already printed a clean per-file header above.
    printf '%s\n' "$out" | tail -n +3
  else
    printf '%s\n' "$out" >&2
    return "$status"
  fi
}

# Extract current version from the first `version = "..."` line in pyproject.toml.
CURRENT=$(sed -n 's/^version = "\([^"]*\)"/\1/p' "$PYPROJECT" | head -1)

if [[ -z "$CURRENT" ]]; then
  echo "ERROR: Could not read version from $PYPROJECT" >&2
  exit 1
fi

IFS='.' read -r MAJOR MINOR PATCH <<< "$CURRENT"

case "$BUMP" in
  major)
    MAJOR=$((MAJOR + 1))
    MINOR=0
    PATCH=0
    ;;
  minor)
    MINOR=$((MINOR + 1))
    PATCH=0
    ;;
  patch)
    PATCH=$((PATCH + 1))
    ;;
  *)
    echo "Usage: $(basename "$0") [major|minor|patch]" >&2
    exit 1
    ;;
esac

NEW="$MAJOR.$MINOR.$PATCH"

# Snapshot every target before we touch it so `show_diff` can produce an
# accurate per-file snippet regardless of whatever unrelated edits the
# working tree already has.
snapshot_file "$PYPROJECT"
snapshot_file "$README"
snapshot_file "$VERSION_TEST"

# pyproject.toml: replace only the first `version = "..."` line (project version,
# not any dependency specifier that happens to match).
sed -i "0,/^version = \"$CURRENT\"/s//version = \"$NEW\"/" "$PYPROJECT"

# README.md: replace the first `v<old>` occurrence (header line).
sed -i "0,/v$CURRENT/s//v$NEW/" "$README"

# tests/unit/test_app_version.py: docstring phrase, dynamic method name, and
# quoted version literals all move together.
OLD_SAFE="${CURRENT//./_}"
NEW_SAFE="${NEW//./_}"
sed -i \
  -e "s/version must be $CURRENT/version must be $NEW/" \
  -e "s/test_version_is_$OLD_SAFE/test_version_is_$NEW_SAFE/" \
  -e "s/\"$CURRENT\"/\"$NEW\"/g" \
  "$VERSION_TEST"

show_diff "$PYPROJECT"
show_diff "$README"
show_diff "$VERSION_TEST"

echo
echo "$CURRENT -> $NEW"
