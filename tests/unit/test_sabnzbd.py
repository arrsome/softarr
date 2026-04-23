import pytest

from softarr.integrations.sabnzbd import SABnzbdClient, SABnzbdConfig, SABnzbdError


class TestSABnzbdConfig:
    def test_missing_url_raises(self):
        with pytest.raises(SABnzbdError, match="URL is not configured"):
            SABnzbdClient(SABnzbdConfig(url="", api_key="abc"))

    def test_missing_api_key_raises(self):
        with pytest.raises(SABnzbdError, match="API key is not configured"):
            SABnzbdClient(SABnzbdConfig(url="http://localhost:8080", api_key=""))

    def test_valid_config(self):
        client = SABnzbdClient(
            SABnzbdConfig(url="http://localhost:8080/", api_key="testkey")
        )
        assert client.config.url == "http://localhost:8080"
        assert client.config.api_key == "testkey"

    def test_url_trailing_slash_stripped(self):
        client = SABnzbdClient(
            SABnzbdConfig(url="http://sab.local:8080///", api_key="key")
        )
        assert client.config.url == "http://sab.local:8080"

    def test_base_params(self):
        client = SABnzbdClient(SABnzbdConfig(url="http://sab:8080", api_key="mykey"))
        params = client._base_params()
        assert params["apikey"] == "mykey"
        assert params["output"] == "json"


class TestSABnzbdSendValidation:
    def test_empty_url_raises(self):
        client = SABnzbdClient(SABnzbdConfig(url="http://sab:8080", api_key="key"))

        @pytest.mark.asyncio
        async def _test():
            with pytest.raises(SABnzbdError, match="Download URL is required"):
                await client.send_url("")

    def test_bad_scheme_raises(self):
        client = SABnzbdClient(SABnzbdConfig(url="http://sab:8080", api_key="key"))

        @pytest.mark.asyncio
        async def _test():
            with pytest.raises(SABnzbdError, match="must start with http"):
                await client.send_url("ftp://bad.url/file.nzb")
