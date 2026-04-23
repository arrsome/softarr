"""Post-processing filters for release search results.

Applies user-selected search modes (regex, fuzzy, exact, boolean) to a list
of search result dicts after the adapter has already returned them. All
filtering operates on the display name / raw release name fields.

Modes:
  standard  -- no extra filtering; adapter match_score already ranks results
  regex     -- filter by compiled regex pattern against release name
  fuzzy     -- retain results where character-overlap ratio >= threshold
  exact     -- case-insensitive whole-word substring match
  boolean   -- simple AND / OR / NOT expression parser
"""

import re
from difflib import SequenceMatcher
from typing import Any


def _release_name(result: dict[str, Any]) -> str:
    """Return the best available name field for matching."""
    return (result.get("name") or result.get("display_name") or "").lower()


# ---------------------------------------------------------------------------
# Individual filter implementations
# ---------------------------------------------------------------------------


def apply_regex(results: list[dict[str, Any]], pattern: str) -> list[dict[str, Any]]:
    """Filter results whose name matches the regex pattern.

    Raises ``ValueError`` if the pattern is invalid.
    """
    try:
        compiled = re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise ValueError(f"Invalid regex pattern: {exc}") from exc
    return [r for r in results if compiled.search(_release_name(r))]


def apply_fuzzy(
    results: list[dict[str, Any]], query: str, threshold: float = 0.4
) -> list[dict[str, Any]]:
    """Filter results where character-overlap ratio with the query meets the threshold.

    Uses stdlib ``difflib.SequenceMatcher`` -- no external dependencies.
    The threshold (0.0--1.0) controls how loose the matching is; 0.4 is a
    reasonable default for short release names with typos.
    """
    q = query.lower().strip()
    if not q:
        return results

    def ratio(name: str) -> float:
        return SequenceMatcher(None, q, name).ratio()

    return [r for r in results if ratio(_release_name(r)) >= threshold]


def apply_exact(results: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """Filter results that contain the query as a case-insensitive substring."""
    q = query.lower().strip()
    if not q:
        return results
    return [r for r in results if q in _release_name(r)]


def apply_boolean(results: list[dict[str, Any]], expr: str) -> list[dict[str, Any]]:
    """Filter results using a simple AND / OR / NOT boolean expression.

    Syntax:
      word1 AND word2   -- both must appear
      word1 OR word2    -- either must appear
      NOT word1         -- word1 must not appear
      word1 NOT word2   -- word1 must appear, word2 must not

    Operators are case-insensitive. Parentheses are not supported.
    Evaluated left-to-right in a single pass; AND binds tighter than OR.
    """

    def _match(name: str, expression: str) -> bool:
        # Tokenise: split on whitespace, preserve AND/OR/NOT
        tokens = expression.strip().split()
        if not tokens:
            return True

        def _eval_and_clause(clause_tokens: list[str]) -> bool:
            """Evaluate a sequence of terms joined implicitly or by AND / NOT."""
            must_contain: list[str] = []
            must_not_contain: list[str] = []
            negate_next = False
            for tok in clause_tokens:
                upper = tok.upper()
                if upper == "AND":
                    continue
                if upper == "NOT":
                    negate_next = True
                    continue
                if negate_next:
                    must_not_contain.append(tok.lower())
                    negate_next = False
                else:
                    must_contain.append(tok.lower())
            return all(t in name for t in must_contain) and not any(
                t in name for t in must_not_contain
            )

        # Split on OR into clauses
        or_clauses: list[list[str]] = []
        current: list[str] = []
        for tok in tokens:
            if tok.upper() == "OR":
                or_clauses.append(current)
                current = []
            else:
                current.append(tok)
        or_clauses.append(current)

        return any(_eval_and_clause(clause) for clause in or_clauses)

    q = expr.strip()
    if not q:
        return results
    return [r for r in results if _match(_release_name(r), q)]


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

VALID_MODES = {"standard", "regex", "fuzzy", "exact", "boolean"}


def filter_results(
    results: list[dict[str, Any]],
    mode: str,
    query: str,
) -> list[dict[str, Any]]:
    """Apply the selected search mode filter to the results list.

    ``mode`` must be one of ``VALID_MODES``. ``query`` is the freeform
    search string entered by the user.

    Returns the filtered list unchanged for ``standard`` mode (the adapter's
    own match_score ranking already handles relevance).
    """
    mode = (mode or "standard").lower().strip()
    if mode not in VALID_MODES:
        mode = "standard"
    if not query or mode == "standard":
        return results
    if mode == "regex":
        return apply_regex(results, query)
    if mode == "fuzzy":
        return apply_fuzzy(results, query)
    if mode == "exact":
        return apply_exact(results, query)
    if mode == "boolean":
        return apply_boolean(results, query)
    return results
