"""Unit tests for Phase 4 analysis extensions.

Covers:
  - Item 8: Signature verification (SignatureVerifier)
  - Item 9: Vendor checksum fetching (vendor_checksums)
  - Item 10: VirusTotal URL submission
  - Item 19: Hash intelligence service (HashIntelligenceService)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Item 10 -- VirusTotal submission
# ---------------------------------------------------------------------------


class TestVtSubmission:
    @pytest.mark.asyncio
    async def test_submits_url_when_hash_not_found(self):
        from softarr.analysis.hash_sources.virustotal import submit_url_for_analysis

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json = MagicMock(return_value={"data": {"id": "abc123"}})

        with patch(
            "softarr.analysis.hash_sources.virustotal.httpx.AsyncClient"
        ) as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            result = await submit_url_for_analysis(
                "https://example.com/app.exe", "testkey"
            )

        assert result is not None
        assert result["analysis_id"] == "abc123"

    @pytest.mark.asyncio
    async def test_returns_none_on_network_error(self):
        import httpx

        from softarr.analysis.hash_sources.virustotal import submit_url_for_analysis

        with patch(
            "softarr.analysis.hash_sources.virustotal.httpx.AsyncClient"
        ) as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(side_effect=httpx.RequestError("timeout"))
            mock_client_cls.return_value = mock_client

            result = await submit_url_for_analysis(
                "https://example.com/app.exe", "testkey"
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_no_api_key(self):
        from softarr.analysis.hash_sources.virustotal import submit_url_for_analysis

        result = await submit_url_for_analysis("https://example.com/app.exe", "")
        assert result is None


# ---------------------------------------------------------------------------
# Item 9 -- Vendor checksum fetching
# ---------------------------------------------------------------------------


class TestVendorChecksums:
    @pytest.mark.asyncio
    async def test_parses_sha256sum_format(self):
        from softarr.analysis.hash_sources.vendor_checksums import (
            _parse_checksum_content,
        )

        content = "abc123" + "0" * 58 + "  myapp-1.0.0.zip\n"
        result = _parse_checksum_content(content, "myapp-1.0.0.zip")
        assert result is not None
        assert "sha256" in result
        assert len(result["sha256"]) == 64

    @pytest.mark.asyncio
    async def test_returns_none_for_empty_content(self):
        from softarr.analysis.hash_sources.vendor_checksums import (
            _parse_checksum_content,
        )

        result = _parse_checksum_content("", "myapp.zip")
        assert result is None

    @pytest.mark.asyncio
    async def test_candidate_urls_built_correctly(self):
        from softarr.analysis.hash_sources.vendor_checksums import _candidate_urls

        urls = _candidate_urls(
            "https://example.com/releases/myapp-1.0.zip", "myapp-1.0.zip"
        )
        assert "https://example.com/releases/myapp-1.0.zip.sha256" in urls
        assert "https://example.com/releases/SHA256SUMS" in urls
        assert "https://example.com/releases/checksums.txt" in urls

    @pytest.mark.asyncio
    async def test_fetch_returns_none_on_all_404(self):
        from softarr.analysis.hash_sources.vendor_checksums import (
            fetch_vendor_checksums,
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 404

        with patch(
            "softarr.analysis.hash_sources.vendor_checksums.httpx.AsyncClient"
        ) as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_client

            result = await fetch_vendor_checksums(
                "https://example.com/app.exe", "softarr.exe"
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_fetch_returns_checksum_when_found(self):
        from softarr.analysis.hash_sources.vendor_checksums import (
            fetch_vendor_checksums,
        )

        hash_val = "a" * 64
        mock_resp_404 = MagicMock(status_code=404)
        mock_resp_ok = MagicMock(
            status_code=200,
            content=f"{hash_val}  app.exe\n".encode(),
            text=f"{hash_val}  app.exe\n",
        )

        responses = [mock_resp_404, mock_resp_ok]

        with patch(
            "softarr.analysis.hash_sources.vendor_checksums.httpx.AsyncClient"
        ) as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.get = AsyncMock(side_effect=responses)
            mock_cls.return_value = mock_client

            result = await fetch_vendor_checksums(
                "https://example.com/app.exe", "softarr.exe"
            )

        assert result is not None
        assert result["sha256"] == hash_val


# ---------------------------------------------------------------------------
# Item 8 -- Signature verifier
# ---------------------------------------------------------------------------


class TestSignatureVerifier:
    @pytest.mark.asyncio
    async def test_verify_gpg_returns_no_signature_for_empty_bytes(self):
        from softarr.analysis.signature_verifier import SignatureVerifier

        verifier = SignatureVerifier()
        result = await verifier.verify_gpg(b"content", b"")
        assert result == "no_signature"

    @pytest.mark.asyncio
    async def test_verify_gpg_returns_error_when_gnupg_not_installed(self):
        from softarr.analysis.signature_verifier import SignatureVerifier

        verifier = SignatureVerifier()
        with patch.dict("sys.modules", {"gnupg": None}):
            result = await verifier.verify_gpg(b"content", b"sig data")
        assert result == "error"

    @pytest.mark.asyncio
    async def test_verify_sigstore_returns_error_when_not_installed(self):
        from softarr.analysis.signature_verifier import SignatureVerifier

        verifier = SignatureVerifier()
        with patch.dict("sys.modules", {"sigstore": None}):
            result = await verifier.verify_sigstore(
                "https://example.com/app.exe",
                "https://example.com/app.exe.sigstore",
            )
        assert result == "error"

    @pytest.mark.asyncio
    async def test_verify_from_assets_returns_no_signature_for_empty_list(self):
        from softarr.analysis.signature_verifier import SignatureVerifier

        verifier = SignatureVerifier()
        result = await verifier.verify_from_assets("https://example.com/app.exe", [])
        assert result == "no_signature"

    @pytest.mark.asyncio
    async def test_verify_from_assets_returns_no_signature_when_download_fails(self):
        from softarr.analysis.signature_verifier import SignatureVerifier

        verifier = SignatureVerifier()
        with patch(
            "softarr.analysis.signature_verifier._download_bytes", return_value=None
        ):
            result = await verifier.verify_from_assets(
                "https://example.com/app.exe",
                ["https://example.com/app.exe.sig"],
            )
        assert result == "no_signature"


# ---------------------------------------------------------------------------
# Item 19 -- Hash intelligence service
# ---------------------------------------------------------------------------


class TestHashIntelligenceService:
    def _make_service(self):
        from softarr.services.hash_intelligence_service import HashIntelligenceService

        db = AsyncMock()
        db.execute = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()

        ini = MagicMock()
        ini.get = MagicMock(
            side_effect=lambda k: {
                "virustotal_enabled": "false",
                "nsrl_enabled": "false",
                "circl_hashlookup_enabled": "false",
                "malwarebazaar_enabled": "false",
                "misp_warninglists_enabled": "false",
                "hash_recheck_interval_hours": "24",
            }.get(k, "false")
        )

        return HashIntelligenceService(db, ini), db, ini

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_sha256(self):
        svc, _, _ = self._make_service()
        import uuid

        results = await svc.check_all_sources(uuid.uuid4())
        assert results == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_all_sources_disabled(self):
        svc, _, _ = self._make_service()
        import uuid

        results = await svc.check_all_sources(uuid.uuid4(), sha256="a" * 64)
        assert results == []

    @pytest.mark.asyncio
    async def test_virustotal_known_bad_creates_record(self):
        import uuid

        from softarr.services.hash_intelligence_service import HashIntelligenceService

        db = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()

        ini = MagicMock()
        ini.get = MagicMock(
            side_effect=lambda k: {
                "virustotal_enabled": "true",
                "virustotal_api_key": "testkey",
                "nsrl_enabled": "false",
                "circl_hashlookup_enabled": "false",
                "malwarebazaar_enabled": "false",
                "misp_warninglists_enabled": "false",
                "hash_recheck_interval_hours": "24",
            }.get(k, "false")
        )

        svc = HashIntelligenceService(db, ini)

        vt_result = {
            "found": True,
            "malicious_count": 5,
            "total_engines": 70,
            "permalink": "https://vt.example/report",
        }

        with patch(
            "softarr.analysis.hash_sources.virustotal.lookup",
            new_callable=AsyncMock,
            return_value=vt_result,
        ):
            records = await svc.check_all_sources(uuid.uuid4(), sha256="a" * 64)

        assert len(records) == 1
        assert records[0].verdict == "known_bad"
        assert records[0].source == "virustotal"

    @pytest.mark.asyncio
    async def test_circl_known_good_creates_record(self):
        import uuid

        from softarr.services.hash_intelligence_service import HashIntelligenceService

        db = AsyncMock()
        db.add = MagicMock()
        db.commit = AsyncMock()

        ini = MagicMock()
        ini.get = MagicMock(
            side_effect=lambda k: {
                "virustotal_enabled": "false",
                "nsrl_enabled": "false",
                "circl_hashlookup_enabled": "true",
                "malwarebazaar_enabled": "false",
                "misp_warninglists_enabled": "false",
                "hash_recheck_interval_hours": "24",
            }.get(k, "false")
        )

        svc = HashIntelligenceService(db, ini)

        circl_result = {
            "found": True,
            "product_name": "TestApp",
            "publisher": "TestCorp",
        }

        with patch(
            "softarr.analysis.hash_sources.circl_hashlookup.lookup",
            new_callable=AsyncMock,
            return_value=circl_result,
        ):
            records = await svc.check_all_sources(uuid.uuid4(), sha256="b" * 64)

        assert len(records) == 1
        assert records[0].verdict == "known_good"
        assert records[0].source == "circl"
