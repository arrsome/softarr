import pytest

from softarr.adapters.usenet import UsenetAdapter, UsenetIndexerConfig


class TestUsenetAdapter:
    @pytest.mark.asyncio
    async def test_no_indexers_returns_empty(self):
        adapter = UsenetAdapter(indexers=[])
        results = await adapter.search_releases({"canonical_name": "Test"})
        assert results == []

    @pytest.mark.asyncio
    async def test_disabled_indexer_skipped(self):
        adapter = UsenetAdapter(
            indexers=[
                UsenetIndexerConfig(
                    name="Disabled",
                    url="http://fake.indexer/api",
                    api_key="key",
                    enabled=False,
                )
            ]
        )
        results = await adapter.search_releases({"canonical_name": "Test"})
        assert results == []

    def test_version_extraction_standard(self):
        assert UsenetAdapter._extract_version("MyApp v1.2.3") == "1.2.3"

    def test_version_extraction_no_v_prefix(self):
        assert UsenetAdapter._extract_version("MyApp 2.0.1") == "2.0.1"

    def test_version_extraction_four_part(self):
        assert UsenetAdapter._extract_version("App 1.2.3.4 Setup") == "1.2.3.4"

    def test_version_extraction_no_version(self):
        assert UsenetAdapter._extract_version("NoVersionHere") == "unknown"

    def test_version_extraction_in_filename(self):
        assert UsenetAdapter._extract_version("app-crack-3.14.exe") == "3.14"

    def test_parse_empty_xml(self):
        adapter = UsenetAdapter()
        indexer = UsenetIndexerConfig(name="T", url="http://x", api_key="k")
        results = adapter._parse_newznab_response(
            "<rss><channel></channel></rss>",
            indexer,
            {"supported_os": ["windows"]},
        )
        assert results == []

    def test_parse_newznab_item(self):
        adapter = UsenetAdapter()
        indexer = UsenetIndexerConfig(name="TestIdx", url="http://x", api_key="k")
        xml = """<rss><channel>
            <item>
                <title>TestApp v2.5.0 x64</title>
                <link>http://indexer/getnzb/abc123</link>
                <pubDate>Mon, 01 Jan 2025 00:00:00 +0000</pubDate>
            </item>
        </channel></rss>"""
        results = adapter._parse_newznab_response(
            xml,
            indexer,
            {"canonical_name": "TestApp", "supported_os": ["windows", "linux"]},
        )
        assert len(results) == 1
        assert results[0].name == "TestApp v2.5.0 x64"
        assert results[0].version == "2.5.0"
        assert results[0].source_type == "usenet"
        assert results[0].source_origin == "http://indexer/getnzb/abc123"
        assert results[0].supported_os == ["windows", "linux"]

    def test_parse_newznab_with_enclosure(self):
        adapter = UsenetAdapter()
        indexer = UsenetIndexerConfig(name="T", url="http://x", api_key="k")
        xml = """<rss><channel>
            <item>
                <title>App 1.0</title>
                <link>http://indexer/details/123</link>
                <enclosure url="http://indexer/getnzb/123?dl=1" length="5000" type="application/x-nzb"/>
            </item>
        </channel></rss>"""
        results = adapter._parse_newznab_response(
            xml, indexer, {"canonical_name": "App"}
        )
        assert len(results) == 1
        assert results[0].source_origin == "http://indexer/getnzb/123?dl=1"

    @pytest.mark.asyncio
    async def test_fetch_release_details(self):
        adapter = UsenetAdapter()
        result = await adapter.fetch_release_details("http://nzb.url/123")
        assert result["type"] == "nzb"
        assert result["handoff"] == "sabnzbd"
