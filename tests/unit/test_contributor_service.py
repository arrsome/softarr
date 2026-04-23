"""Unit tests for ContributorService."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from softarr.services.contributor_service import (
    ContributorService,
    _build_summary,
    _topic_for,
)


class TestTopicFor:
    def test_bug_fix_keyword(self):
        assert _topic_for("Fix login redirect 500 error") == "Bug fixes"

    def test_staging_queue(self):
        assert _topic_for("Add staging queue page and bulk approve") == "Staging queue"

    def test_swipe_gestures(self):
        assert _topic_for("Add swipe gesture support") == "Swipe gestures"

    def test_skip_merge(self):
        assert _topic_for("Merge pull request #12") == ""

    def test_skip_bump_version(self):
        assert _topic_for("Bump version to 1.2.0") == ""

    def test_unrecognised_returns_empty(self):
        assert _topic_for("Something completely different") == ""


class TestBuildSummary:
    def test_returns_topic_labels(self):
        subjects = [
            "Add staging queue page",
            "Fix login redirect",
            "Add swipe gestures",
        ]
        summary = _build_summary(subjects)
        assert "Staging queue" in summary
        assert "Bug fixes" in summary
        assert "Swipe gestures" in summary

    def test_empty_subjects_fallback(self):
        summary = _build_summary([])
        assert "commit" in summary

    def test_only_skipped_subjects_fallback(self):
        summary = _build_summary(["Merge pull request #1", "Bump version to 2.0"])
        assert "commit" in summary

    def test_capped_at_six_topics(self):
        subjects = [
            "Add staging queue page",
            "Fix login redirect",
            "Add swipe gestures",
            "Add AI assistant",
            "Fix usenet search",
            "Add push notifications",
            "Refactor release service",
            "Add torznab support",
        ]
        summary = _build_summary(subjects)
        # Six topics max -- count commas (N topics = N-1 commas)
        assert summary.count(",") <= 5

    def test_deduplicates_topics(self):
        subjects = ["Fix login redirect", "Fix 500 error", "Fix auth bug"]
        summary = _build_summary(subjects)
        assert summary.count("Bug fixes") == 1


class TestContributorService:
    def _make_contributor_response(self):
        return [
            {
                "login": "arrsome",
                "avatar_url": "https://avatars.githubusercontent.com/u/1?v=4",
                "html_url": "https://github.com/arrsome",
                "contributions": 42,
                "type": "User",
            }
        ]

    def _make_commits_response(self):
        return [
            {"commit": {"message": "Add PWA support"}},
            {"commit": {"message": "Fix login redirect bug"}},
        ]

    @pytest.mark.asyncio
    async def test_returns_contributors_with_summary(self):
        svc = ContributorService(repo="arrsome/softarr")

        contrib_resp = MagicMock()
        contrib_resp.status_code = 200
        contrib_resp.json.return_value = self._make_contributor_response()

        commits_resp = MagicMock()
        commits_resp.status_code = 200
        commits_resp.json.return_value = self._make_commits_response()

        mock_client = AsyncMock()
        mock_client.get.side_effect = [contrib_resp, commits_resp]
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "softarr.services.contributor_service.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await svc.get_contributors()

        assert len(result) == 1
        assert result[0]["login"] == "arrsome"
        assert result[0]["commits"] == 42
        assert isinstance(result[0]["summary"], str) and len(result[0]["summary"]) > 0

    @pytest.mark.asyncio
    async def test_caches_result(self):
        svc = ContributorService(repo="arrsome/softarr")

        contrib_resp = MagicMock()
        contrib_resp.status_code = 200
        contrib_resp.json.return_value = self._make_contributor_response()

        commits_resp = MagicMock()
        commits_resp.status_code = 200
        commits_resp.json.return_value = []

        mock_client = AsyncMock()
        mock_client.get.side_effect = [contrib_resp, commits_resp]
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "softarr.services.contributor_service.httpx.AsyncClient",
            return_value=mock_client,
        ):
            first = await svc.get_contributors()
            # Second call should return cached data without hitting the client again
            second = await svc.get_contributors()

        assert first is second
        # AsyncClient was only constructed once
        assert mock_client.get.call_count == 2  # contributors + commits, not doubled

    @pytest.mark.asyncio
    async def test_falls_back_to_git_on_api_error(self):
        """On GitHub API error, service falls back to git log rather than returning []."""
        svc = ContributorService(repo="arrsome/softarr")

        mock_client = AsyncMock()
        mock_client.get.side_effect = Exception("network error")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        git_contributors = [
            {
                "login": "testuser",
                "display_name": "Test User",
                "avatar_url": "https://github.com/testuser.png",
                "html_url": "https://github.com/testuser",
                "commits": 5,
                "summary": "Various contributions",
            }
        ]

        with (
            patch(
                "softarr.services.contributor_service.httpx.AsyncClient",
                return_value=mock_client,
            ),
            patch.object(svc, "_fetch_from_git", return_value=git_contributors),
        ):
            result = await svc.get_contributors()

        assert result == git_contributors

    @pytest.mark.asyncio
    async def test_skips_bot_accounts(self):
        """Bot-only GitHub response triggers git fallback (no human contributors returned via API)."""
        svc = ContributorService(repo="arrsome/softarr")

        contrib_resp = MagicMock()
        contrib_resp.status_code = 200
        contrib_resp.json.return_value = [
            {
                "login": "dependabot[bot]",
                "avatar_url": "https://avatars.githubusercontent.com/u/2?v=4",
                "html_url": "https://github.com/apps/dependabot",
                "contributions": 5,
                "type": "Bot",
            }
        ]

        mock_client = AsyncMock()
        mock_client.get.return_value = contrib_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        # When GitHub returns only bots, _fetch_from_git is called as fallback.
        # Mock it to return empty so the test stays deterministic.
        with (
            patch(
                "softarr.services.contributor_service.httpx.AsyncClient",
                return_value=mock_client,
            ),
            patch.object(svc, "_fetch_from_git", return_value=[]),
        ):
            result = await svc.get_contributors()

        assert result == []
