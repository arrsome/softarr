"""Fetch and summarise repository contributors.

Primary source: GitHub REST API (requires token for private repos).
Fallback: local ``git log`` -- always available, no auth required.

Results are cached in-memory for one hour to avoid repeated work on every
page load.
"""

import asyncio
import re
import time
from dataclasses import dataclass, field

import httpx

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

GITHUB_API = "https://api.github.com"

_SKIP_RE = re.compile(
    r"^(merge\b|bump version|update changelog|chore\b|wip\b|initial commit|auto-trigger)",
    re.IGNORECASE,
)

# Patterns that map commit subjects to clean topic labels.
# Each entry: (label, [regex fragments]).  First match wins (case-insensitive).
_TOPIC_RULES: list[tuple[str, list[str]]] = [
    ("UI design", [r"\bui\b.*redesign", r"redesign.*ui", r"visual language"]),
    ("Staging queue", [r"staging.queue", r"bulk.approv", r"bulk.reject"]),
    ("Swipe gestures", [r"swipe"]),
    ("AI assistant", [r"\bai\b.*(assistant|frontend|mode)", r"ai assistant"]),
    ("Search", [r"search.mode", r"search.enhanc", r"release.search"]),
    ("Push notifications", [r"push.notif", r"vapid", r"web.push"]),
    ("PWA", [r"\bpwa\b", r"service.worker", r"manifest"]),
    ("Anti-piracy filter", [r"anti.pirac", r"antipirac"]),
    ("2FA / TOTP", [r"\btotp\b", r"two.factor", r"2fa"]),
    ("Download clients", [r"sabnzbd", r"qbittorrent", r"download.client"]),
    ("Usenet", [r"usenet", r"newznab", r"nzb"]),
    ("Torznab", [r"torznab"]),
    (
        "Hash intelligence",
        [r"hash.intel", r"virustotal", r"nsrl", r"malwarebazar", r"circl"],
    ),
    ("Release rules", [r"release.rules", r"version.pin", r"auto.reject"]),
    ("Workflow", [r"workflow", r"staging.transition", r"approval"]),
    ("GitHub Actions", [r"github.actions", r"ci.workflow", r"automated.release"]),
    ("About page", [r"about.page", r"about section", r"contributor"]),
    ("Settings", [r"settings.*ui", r"settings.*section", r"ini.settings"]),
    ("Accessibility", [r"wcag", r"accessib", r"aria"]),
    (
        "Bug fixes",
        [r"\bfix\b", r"\bbug\b", r"\bcorrect\b", r"\bresolve\b", r"\brepair\b"],
    ),
    ("Refactoring", [r"refactor", r"move.*to", r"restructur", r"packaging"]),
    (
        "Documentation",
        [r"\bdocs?\b", r"readme", r"roadmap", r"changelog", r"architecture"],
    ),
    ("Dependencies", [r"dependenc", r"requirements", r"pyproject"]),
]

_TOPIC_RE: list[tuple[str, re.Pattern]] = [
    (label, re.compile("|".join(patterns), re.IGNORECASE))
    for label, patterns in _TOPIC_RULES
]


def _topic_for(subject: str) -> str:
    """Return the best-matching topic label for a commit subject, or ''."""
    if _SKIP_RE.search(subject.strip()):
        return ""
    for label, pattern in _TOPIC_RE:
        if pattern.search(subject):
            return label
    return ""


def _build_summary(subjects: list[str]) -> str:
    """Produce a clean comma-separated list of contribution topics.

    Scans commit subjects, maps them to topic labels, deduplicates, and
    returns up to six topics joined by commas.  Falls back to a commit-count
    description when no subjects match known topics.
    """
    seen: dict[str, int] = {}
    for s in subjects:
        topic = _topic_for(s.strip())
        if topic:
            seen[topic] = seen.get(topic, 0) + 1

    if not seen:
        n = len(subjects)
        return f"{n} commit{'s' if n != 1 else ''} across various areas"

    # Sort by frequency descending, cap at six topics
    top = sorted(seen, key=lambda t: seen[t], reverse=True)[:6]
    return ", ".join(top)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _github_login_from_email(email: str) -> str:
    """Best-effort GitHub username derivation from a commit e-mail address.

    Handles:
    - ``123456+username@users.noreply.github.com``
    - ``username@users.noreply.github.com``
    - Falls back to empty string.
    """
    noreply = re.match(r"(?:\d+\+)?([A-Za-z0-9_-]+)@users\.noreply\.github\.com", email)
    if noreply:
        return noreply.group(1)
    return ""


# ---------------------------------------------------------------------------
# Cache entry
# ---------------------------------------------------------------------------


@dataclass
class _CacheEntry:
    data: list[dict]
    fetched_at: float = field(default_factory=time.monotonic)

    def is_fresh(self, ttl: float = 3600.0) -> bool:
        return (time.monotonic() - self.fetched_at) < ttl


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class ContributorService:
    """Fetch contributor data from GitHub with in-memory caching."""

    def __init__(self, repo: str, token: str = "", repo_path: str = ".") -> None:
        """
        Args:
            repo:       GitHub repository in ``owner/name`` format.
            token:      Optional GitHub personal access token.  Raises the
                        unauthenticated rate limit from 60 to 5000 req/hr.
            repo_path:  Local filesystem path to the git repository root.
                        Used by the git-log fallback when GitHub is unavailable.
        """
        self._repo = repo.strip("/")
        self._repo_path = repo_path
        self._headers: dict[str, str] = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if token:
            self._headers["Authorization"] = f"Bearer {token}"
        self._cache: _CacheEntry | None = None

    async def get_contributors(self) -> list[dict]:
        """Return a list of contributor dicts, using cache if still fresh.

        Each dict contains:
            login       -- GitHub username
            avatar_url  -- URL to their avatar image
            html_url    -- Link to their GitHub profile
            commits     -- Total commit count in this repo
            summary     -- Short plain-English summary of contributions
        """
        if self._cache and self._cache.is_fresh():
            return self._cache.data

        data = await self._fetch()
        self._cache = _CacheEntry(data=data)
        return data

    async def _fetch(self) -> list[dict]:
        try:
            async with httpx.AsyncClient(headers=self._headers, timeout=10.0) as client:
                contributors = await self._fetch_contributors(client)
                if not contributors:
                    # GitHub returned nothing (private repo without token, or network
                    # error) -- fall back to git log so the page is never empty.
                    return await self._fetch_from_git()
                # Enrich each contributor with a commit summary
                for c in contributors:
                    subjects = await self._fetch_commit_subjects(client, c["login"])
                    c["summary"] = _build_summary(subjects)
                return contributors
        except Exception:
            # Gracefully degrade to git-based contributor list
            try:
                return await self._fetch_from_git()
            except Exception:
                return []

    async def _fetch_from_git(self) -> list[dict]:
        """Build contributor list from local git history.

        Does not require network access or a GitHub token.  Avatar URLs point
        to the GitHub username derived from the commit author e-mail (best
        effort -- falls back to a generic placeholder).
        """

        # Run git log synchronously in a thread pool to avoid blocking the
        # event loop.
        def _run_git():
            import subprocess

            proc = subprocess.run(
                ["git", "log", "--format=%an\t%ae\t%s"],
                capture_output=True,
                text=True,
                cwd=self._repo_path,
            )
            return proc.stdout if proc.returncode == 0 else ""

        output = await asyncio.get_event_loop().run_in_executor(None, _run_git)

        # Aggregate per author
        from collections import defaultdict

        author_subjects: dict[str, list[str]] = defaultdict(list)
        author_emails: dict[str, str] = {}
        author_commits: dict[str, int] = defaultdict(int)

        for line in output.splitlines():
            parts = line.split("\t", 2)
            if len(parts) < 3:
                continue
            name, email, subject = parts
            # Skip bot commits
            if "[bot]" in name or not name.strip():
                continue
            author_subjects[name].append(subject)
            author_emails[name] = email
            author_commits[name] += 1

        # Sort by commit count descending
        contributors = []
        for name in sorted(
            author_commits, key=lambda n: author_commits[n], reverse=True
        ):
            email = author_emails[name]
            # Prefer the noreply GitHub username, then the local part of the email
            # address (which for most developers matches their GitHub login).
            # Never guess from the display name -- that produces wrong profiles.
            gh_login = _github_login_from_email(email) or email.split("@")[0]
            avatar_url = f"https://github.com/{gh_login}.png"
            html_url = f"https://github.com/{gh_login}"
            contributors.append(
                {
                    "login": gh_login,
                    "display_name": name,
                    "avatar_url": avatar_url,
                    "html_url": html_url,
                    "commits": author_commits[name],
                    "summary": _build_summary(author_subjects[name]),
                }
            )
        return contributors

    async def _fetch_contributors(self, client: httpx.AsyncClient) -> list[dict]:
        url = f"{GITHUB_API}/repos/{self._repo}/contributors"
        resp = await client.get(url, params={"per_page": 30, "anon": "false"})
        if resp.status_code != 200:
            return []
        raw = resp.json()
        return [
            {
                "login": c.get("login", ""),
                "avatar_url": c.get("avatar_url", ""),
                "html_url": c.get("html_url", ""),
                "commits": c.get("contributions", 0),
                "summary": "",
            }
            for c in raw
            if c.get("type") == "User"
        ]

    async def _fetch_commit_subjects(
        self, client: httpx.AsyncClient, login: str
    ) -> list[str]:
        """Fetch the last 20 commit subjects for a specific author."""
        url = f"{GITHUB_API}/repos/{self._repo}/commits"
        resp = await client.get(url, params={"author": login, "per_page": 20})
        if resp.status_code != 200:
            return []
        commits = resp.json()
        subjects: list[str] = []
        for c in commits:
            msg = (
                c.get("commit", {})
                .get("message", "")
                .splitlines()[0]  # first line only
            )
            if msg:
                subjects.append(msg)
        return subjects
