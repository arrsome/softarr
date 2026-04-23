#!/usr/bin/env bash
# scripts/commit.sh -- stage specific files and create a git commit.
#
# Intended for use by AI coding agents (and humans) as the final step of a
# task. Takes explicit filenames so we never accidentally stage secrets via
# `git add .` or `git add -A`, and takes the commit title/message as separate
# arguments so the message body is easy to pass without heredoc gymnastics.
#
# Usage:
#   scripts/commit.sh [-y|--yes] "<filenames>" "<title>" "<message>"
#
# Arguments:
#   -y, --yes  Skip the interactive confirmation prompt. Also honoured via
#              the COMMIT_YES=1 environment variable (useful in CI).
#   filenames  Space-separated list of paths to stage. Paths are relative to
#              the repository root. Globs are NOT expanded -- pass each file
#              explicitly so reviewers can see exactly what was staged.
#   title      Commit title (first line, kept under ~70 chars by convention).
#   message    Commit body. May contain newlines (use $'...' or a literal
#              multi-line string). Explains the WHY, not just the what.
#
# Example:
#   scripts/commit.sh \
#     "src/softarr/auth/sessions.py tests/auth/test_sessions.py" \
#     "Rotate session cookie on privilege change" \
#     "Avoid session fixation after login by reissuing the cookie whenever
#   the authenticated subject changes."
#
# Exit codes:
#   0  Commit created.
#   1  Usage error or staging failure.
#   2  Nothing to commit after staging (working tree clean for these paths).
#   3  Confirmation required but no TTY available (pass -y or COMMIT_YES=1).
#   4  User declined the confirmation prompt.

set -euo pipefail

ASSUME_YES="${COMMIT_YES:-0}"

# Optional leading flag -- keep the positional contract simple otherwise.
if [[ $# -gt 0 && ( "$1" == "-y" || "$1" == "--yes" ) ]]; then
    ASSUME_YES=1
    shift
fi

if [[ $# -ne 3 ]]; then
    cat >&2 <<EOF
Usage: $0 [-y|--yes] "<filenames>" "<title>" "<message>"

  -y, --yes  Skip the interactive confirmation prompt (or set COMMIT_YES=1).
  filenames  Space-separated list of paths to stage (relative to repo root).
  title      Commit title (first line).
  message    Commit body (the WHY, not the what). May contain newlines.

Example:
  $0 "src/foo.py tests/test_foo.py" "Short title" "Why this change..."
EOF
    exit 1
fi

FILENAMES="$1"
TITLE="$2"
MESSAGE="$3"

if [[ -z "$FILENAMES" ]]; then
    echo "ERROR: filenames argument is empty" >&2
    exit 1
fi

if [[ -z "$TITLE" ]]; then
    echo "ERROR: title argument is empty" >&2
    exit 1
fi

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT"

# Split $FILENAMES on whitespace into an array. Intentional word splitting --
# this is exactly what we want here.
# shellcheck disable=SC2206
FILES=($FILENAMES)

# Verify each file exists before staging. Catches typos early and keeps the
# error message attached to the offending path.
for f in "${FILES[@]}"; do
    if [[ ! -e "$f" ]]; then
        # Allow deletions: a file that git knows about but is now gone should
        # still be stageable. `git ls-files --error-unmatch` exits non-zero if
        # git has never heard of the path.
        if ! git ls-files --error-unmatch -- "$f" >/dev/null 2>&1; then
            echo "ERROR: file not found and not tracked by git: $f" >&2
            exit 1
        fi
    fi
done

# Warn if any file looks like it might contain secrets. Non-fatal -- the agent
# or user can override by passing the file anyway, but this gives us a chance
# to catch obvious mistakes.
for f in "${FILES[@]}"; do
    case "$(basename -- "$f")" in
        .env|.env.*|*.pem|*.key|credentials.json|softarr.ini)
            printf 'WARNING: staging %s which looks sensitive; continuing anyway\n' "$f" >&2
            ;;
    esac
done

# Snapshot which files were already staged before we touch the index, so the
# rollback on decline only unstages what *this invocation* added. Anything the
# user had staged beforehand is left exactly as we found it.
PRE_STAGED="$(git diff --cached --name-only)"

git add -- "${FILES[@]}"

# Nothing to commit? Bail with a distinct exit code so callers can tell the
# difference between "you asked me to commit but there were no changes" and
# a hard failure.
if git diff --cached --quiet; then
    echo "Nothing staged -- working tree already clean for these paths." >&2
    exit 2
fi

# Build the commit message. Blank line between title and body per git
# convention, and pass via -F so newlines in $MESSAGE survive intact.
COMMIT_MSG_FILE="$(mktemp)"
trap 'rm -f "$COMMIT_MSG_FILE"' EXIT

{
    printf '%s\n' "$TITLE"
    if [[ -n "$MESSAGE" ]]; then
        printf '\n%s\n' "$MESSAGE"
    fi
} > "$COMMIT_MSG_FILE"

# Compute the files newly added by this invocation, for rollback on decline.
# `comm -23` = lines only in the first (post-stage) set. Sort required.
NEWLY_STAGED="$(
    comm -23 \
        <(git diff --cached --name-only | sort -u) \
        <(printf '%s\n' "$PRE_STAGED" | sort -u)
)"

rollback_staging() {
    if [[ -n "$NEWLY_STAGED" ]]; then
        # shellcheck disable=SC2086
        git reset --quiet HEAD -- $NEWLY_STAGED
    fi
}

# Interactive confirmation. Shows what would be committed so the operator
# (human or agent-via-human) can sanity-check before anything lands.
if [[ "$ASSUME_YES" != "1" ]]; then
    printf '\n==== Commit preview ====\n'
    printf 'Title:\n  %s\n' "$TITLE"
    if [[ -n "$MESSAGE" ]]; then
        printf '\nMessage:\n'
        printf '%s\n' "$MESSAGE" | sed 's/^/  /'
    fi
    printf '\nStaged changes:\n'
    git diff --cached --stat | sed 's/^/  /'
    printf '========================\n\n'

    if [[ ! -t 0 ]]; then
        rollback_staging
        cat >&2 <<EOF
ERROR: confirmation required but stdin is not a TTY.
       Re-run with -y / --yes, or set COMMIT_YES=1 to skip the prompt.
EOF
        exit 3
    fi

    printf 'Proceed with commit? [y/N] '
    read -r reply
    case "$reply" in
        y|Y|yes|YES)
            ;;
        *)
            rollback_staging
            echo "Aborted -- staging rolled back." >&2
            exit 4
            ;;
    esac
fi

git commit -F "$COMMIT_MSG_FILE"

printf '\nCommit created:\n'
git log -1 --format='  %h %s'
