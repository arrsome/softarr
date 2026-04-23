from softarr.adapters.github import GitHubAdapter


class TestGitHubAdapter:
    def test_extract_owner_repo_valid(self):
        adapter = GitHubAdapter()
        assert adapter._extract_owner_repo("owner/repo") == "owner/repo"

    def test_extract_owner_repo_invalid(self):
        adapter = GitHubAdapter()
        assert adapter._extract_owner_repo("just-a-name") is None

    def test_extract_owner_repo_with_dots(self):
        adapter = GitHubAdapter()
        assert adapter._extract_owner_repo("org/my.repo") == "org/my.repo"
