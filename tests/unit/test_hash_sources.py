"""Unit tests for VirusTotal and NIST NSRL hash lookup modules.

All HTTP calls are mocked via pytest-httpx (or unittest.mock).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from softarr.analysis.hash_sources import nsrl, virustotal
from softarr.api.v1 import actions as actions_module
from softarr.core.ini_settings import IniSettingsManager

# ---------------------------------------------------------------------------
# VirusTotal
# ---------------------------------------------------------------------------


class TestVirusTotalLookup:
    SHA = "a" * 64

    async def _call(self, mock_resp):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_resp)
            return await virustotal.lookup(self.SHA, "fake-api-key")

    def _make_resp(self, status_code, body=None):
        resp = MagicMock()
        resp.status_code = status_code
        resp.json = MagicMock(return_value=body or {})
        return resp

    @pytest.mark.asyncio
    async def test_not_found_returns_not_found(self):
        resp = self._make_resp(404)
        result = await self._call(resp)
        assert result == {
            "found": False,
            "malicious_count": 0,
            "total_engines": 0,
            "permalink": "",
        }

    @pytest.mark.asyncio
    async def test_non_200_returns_none(self):
        resp = self._make_resp(500)
        result = await self._call(resp)
        assert result is None

    @pytest.mark.asyncio
    async def test_clean_file_returns_zero_malicious(self):
        body = {
            "data": {
                "attributes": {
                    "last_analysis_stats": {
                        "malicious": 0,
                        "suspicious": 0,
                        "undetected": 70,
                        "harmless": 5,
                    }
                },
                "links": {"self": "https://www.virustotal.com/gui/file/aaa"},
            }
        }
        resp = self._make_resp(200, body)
        result = await self._call(resp)
        assert result is not None
        assert result["found"] is True
        assert result["malicious_count"] == 0
        assert result["total_engines"] == 75
        assert "virustotal.com" in result["permalink"]

    @pytest.mark.asyncio
    async def test_malicious_file_returns_count(self):
        body = {
            "data": {
                "attributes": {
                    "last_analysis_stats": {
                        "malicious": 12,
                        "suspicious": 2,
                        "undetected": 50,
                        "harmless": 0,
                    }
                },
                "links": {"self": "https://www.virustotal.com/gui/file/bbb"},
            }
        }
        resp = self._make_resp(200, body)
        result = await self._call(resp)
        assert result["found"] is True
        assert result["malicious_count"] == 12
        assert result["total_engines"] == 64

    @pytest.mark.asyncio
    async def test_request_error_returns_none(self):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=httpx.RequestError("timeout"))
            result = await virustotal.lookup(self.SHA, "key")
        assert result is None

    @pytest.mark.asyncio
    async def test_malformed_json_returns_none(self):
        resp = self._make_resp(200, {})
        resp.json = MagicMock(side_effect=ValueError("bad json"))
        result = await self._call(resp)
        assert result is None


# ---------------------------------------------------------------------------
# VirusTotal API key test endpoint
# ---------------------------------------------------------------------------


class TestVirusTotalTestEndpoint:
    """Tests for the POST /api/v1/actions/virustotal/test endpoint logic."""

    def _make_ini(self, tmp_path, api_key=""):
        ini = IniSettingsManager(tmp_path / "softarr.ini")
        if api_key:
            ini.set("virustotal_api_key", api_key)
        return ini

    def _make_resp(self, status_code):
        resp = MagicMock()
        resp.status_code = status_code
        return resp

    @pytest.mark.asyncio
    async def test_no_key_raises_400(self, tmp_path):
        ini = self._make_ini(tmp_path)
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await actions_module.test_virustotal_connection(
                ini=ini, _user={"u": "admin"}
            )
        assert exc_info.value.status_code == 400
        assert "No VirusTotal API key" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_valid_key_with_404_response_succeeds(self, tmp_path):
        ini = self._make_ini(tmp_path, api_key="valid-key-12345")
        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=self._make_resp(404))
            result = await actions_module.test_virustotal_connection(
                ini=ini, _user={"u": "admin"}
            )
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_valid_key_with_200_response_succeeds(self, tmp_path):
        ini = self._make_ini(tmp_path, api_key="valid-key-12345")
        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=self._make_resp(200))
            result = await actions_module.test_virustotal_connection(
                ini=ini, _user={"u": "admin"}
            )
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_invalid_key_raises_400(self, tmp_path):
        ini = self._make_ini(tmp_path, api_key="bad-key")
        from fastapi import HTTPException

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=self._make_resp(401))
            with pytest.raises(HTTPException) as exc_info:
                await actions_module.test_virustotal_connection(
                    ini=ini, _user={"u": "admin"}
                )
        assert exc_info.value.status_code == 400
        assert "401" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_network_error_raises_502(self, tmp_path):
        ini = self._make_ini(tmp_path, api_key="some-key")
        from fastapi import HTTPException

        with patch("httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(
                side_effect=httpx.RequestError("connection refused")
            )
            with pytest.raises(HTTPException) as exc_info:
                await actions_module.test_virustotal_connection(
                    ini=ini, _user={"u": "admin"}
                )
        assert exc_info.value.status_code == 502


# ---------------------------------------------------------------------------
# NIST NSRL
# ---------------------------------------------------------------------------


class TestNsrlLookup:
    SHA = "b" * 64

    async def _call(self, mock_resp):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_resp)
            return await nsrl.lookup(self.SHA)

    def _make_resp(self, status_code, body=None):
        resp = MagicMock()
        resp.status_code = status_code
        resp.json = MagicMock(return_value=body or {})
        return resp

    @pytest.mark.asyncio
    async def test_not_found_404(self):
        resp = self._make_resp(404)
        result = await self._call(resp)
        assert result == {"found": False, "product_name": None, "manufacturer": None}

    @pytest.mark.asyncio
    async def test_non_200_returns_none(self):
        resp = self._make_resp(503)
        result = await self._call(resp)
        assert result is None

    @pytest.mark.asyncio
    async def test_found_list_response(self):
        body = [{"ProductName": "LibreOffice", "MfgCode": "TDF"}]
        resp = self._make_resp(200, body)
        result = await self._call(resp)
        assert result is not None
        assert result["found"] is True
        assert result["product_name"] == "LibreOffice"
        assert result["manufacturer"] == "TDF"

    @pytest.mark.asyncio
    async def test_found_results_key_response(self):
        body = {"results": [{"product_name": "7-Zip", "manufacturer": "Igor Pavlov"}]}
        resp = self._make_resp(200, body)
        result = await self._call(resp)
        assert result["found"] is True
        assert result["product_name"] == "7-Zip"

    @pytest.mark.asyncio
    async def test_empty_results_returns_not_found(self):
        body = {"results": []}
        resp = self._make_resp(200, body)
        result = await self._call(resp)
        assert result == {"found": False, "product_name": None, "manufacturer": None}

    @pytest.mark.asyncio
    async def test_request_error_returns_none(self):
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(side_effect=httpx.RequestError("conn refused"))
            result = await nsrl.lookup(self.SHA)
        assert result is None


# ---------------------------------------------------------------------------
# Hash source INI settings -- CIRCL, MalwareBazaar, MISP
# ---------------------------------------------------------------------------


class TestHashSourceIniSettings:
    """Verify the three new hash source INI keys are present and default to false."""

    @pytest.fixture
    def ini(self, tmp_path):
        return IniSettingsManager(tmp_path / "softarr.ini")

    def test_circl_default_is_false(self, ini):
        assert ini.get("circl_hashlookup_enabled") == "false"

    def test_malwarebazaar_default_is_false(self, ini):
        assert ini.get("malwarebazaar_enabled") == "false"

    def test_misp_default_is_false(self, ini):
        assert ini.get("misp_warninglists_enabled") == "false"

    def test_circl_toggle_roundtrip(self, ini):
        ini.set("circl_hashlookup_enabled", "true")
        assert ini.get("circl_hashlookup_enabled") == "true"

    def test_malwarebazaar_toggle_roundtrip(self, ini):
        ini.set("malwarebazaar_enabled", "true")
        assert ini.get("malwarebazaar_enabled") == "true"

    def test_misp_toggle_roundtrip(self, ini):
        ini.set("misp_warninglists_enabled", "true")
        assert ini.get("misp_warninglists_enabled") == "true"
