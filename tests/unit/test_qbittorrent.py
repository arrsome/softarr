"""Unit tests for the qBittorrent integration client."""

import pytest

from softarr.integrations.download_client import (
    AbstractDownloadClient,
    DownloadClientError,
)
from softarr.integrations.qbittorrent import (
    _COMPLETED_STATES,
    _FAILED_STATES,
    QBIT_HASH_PREFIX,
    QBittorrentClient,
    QBittorrentConfig,
    QBittorrentError,
)


class TestQBittorrentConfig:
    def test_missing_url_raises(self):
        with pytest.raises(QBittorrentError, match="URL is not configured"):
            QBittorrentClient(
                QBittorrentConfig(url="", username="admin", password="pass")
            )

    def test_missing_username_raises(self):
        with pytest.raises(QBittorrentError, match="username is not configured"):
            QBittorrentClient(
                QBittorrentConfig(
                    url="http://localhost:8080", username="", password="pass"
                )
            )

    def test_valid_config(self):
        client = QBittorrentClient(
            QBittorrentConfig(
                url="http://localhost:8080/",
                username="admin",
                password="secret",
            )
        )
        assert client.config.username == "admin"

    def test_url_trailing_slash_stripped(self):
        client = QBittorrentClient(
            QBittorrentConfig(
                url="http://qbt.local:8080///",
                username="admin",
                password="pass",
            )
        )
        assert client.config.url == "http://qbt.local:8080"

    def test_default_category(self):
        client = QBittorrentClient(
            QBittorrentConfig(url="http://localhost:8080", username="u", password="p")
        )
        assert client.config.category == "software"

    def test_default_ssl_verify(self):
        client = QBittorrentClient(
            QBittorrentConfig(url="http://localhost:8080", username="u", password="p")
        )
        assert client.config.ssl_verify is True

    def test_default_timeout(self):
        from softarr.integrations.qbittorrent import DEFAULT_TIMEOUT

        client = QBittorrentClient(
            QBittorrentConfig(url="http://localhost:8080", username="u", password="p")
        )
        assert client.config.timeout == DEFAULT_TIMEOUT


class TestQBittorrentInterface:
    def test_implements_abstract_download_client(self):
        """QBittorrentClient must satisfy the AbstractDownloadClient interface."""
        client = QBittorrentClient(
            QBittorrentConfig(url="http://localhost:8080", username="u", password="p")
        )
        assert isinstance(client, AbstractDownloadClient)

    def test_error_extends_download_client_error(self):
        assert issubclass(QBittorrentError, DownloadClientError)

    def test_abstract_cannot_be_instantiated(self):
        with pytest.raises(TypeError):
            AbstractDownloadClient()  # type: ignore[abstract]


class TestQBittorrentSendValidation:
    @pytest.mark.asyncio
    async def test_empty_url_raises(self):
        client = QBittorrentClient(
            QBittorrentConfig(url="http://localhost:8080", username="u", password="p")
        )

        # Patch _request to avoid real HTTP calls
        async def _fake_request(*args, **kwargs):
            raise AssertionError("should not reach _request with empty URL")

        client._request = _fake_request  # type: ignore[method-assign]
        with pytest.raises(QBittorrentError, match="Download URL is required"):
            await client.send_url("")

    @pytest.mark.asyncio
    async def test_empty_file_content_raises(self):
        client = QBittorrentClient(
            QBittorrentConfig(url="http://localhost:8080", username="u", password="p")
        )
        with pytest.raises(QBittorrentError, match=".torrent file content is empty"):
            await client.send_file(b"", "test.torrent")


class TestQBittorrentHashPrefix:
    def test_prefix_value(self):
        assert QBIT_HASH_PREFIX == "qbt:"

    def test_prefix_does_not_match_sabnzbd_format(self):
        # SABnzbd nzo_ids look like "SABnzbd_nzo_abc123" -- never start with "qbt:"
        sabnzbd_id = "SABnzbd_nzo_abc123"
        assert not sabnzbd_id.startswith(QBIT_HASH_PREFIX)

    def test_qbt_id_starts_with_prefix(self):
        hash_hex = "a" * 40
        job_id = f"{QBIT_HASH_PREFIX}{hash_hex}"
        assert job_id.startswith(QBIT_HASH_PREFIX)
        assert job_id[len(QBIT_HASH_PREFIX) :] == hash_hex


class TestQBittorrentStates:
    def test_completed_states_non_empty(self):
        assert len(_COMPLETED_STATES) > 0

    def test_failed_states_non_empty(self):
        assert len(_FAILED_STATES) > 0

    def test_completed_and_failed_disjoint(self):
        overlap = {s.lower() for s in _COMPLETED_STATES} & {
            s.lower() for s in _FAILED_STATES
        }
        assert not overlap, f"States appear in both completed and failed: {overlap}"

    def test_common_completed_states_present(self):
        lower = {s.lower() for s in _COMPLETED_STATES}
        assert "uploading" in lower
        assert "seeding" in lower

    def test_common_failed_states_present(self):
        lower = {s.lower() for s in _FAILED_STATES}
        assert "error" in lower
        assert "missingfiles" in lower
