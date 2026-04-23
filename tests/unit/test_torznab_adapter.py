"""Unit tests for TorznabAdapter (TBI-08)."""

from softarr.adapters.torznab import TorznabAdapter
from softarr.adapters.usenet import UsenetIndexerConfig

SOFTWARE = {
    "canonical_name": "LibreOffice",
    "aliases": ["libreoffice"],
    "expected_publisher": "The Document Foundation",
    "supported_os": ["windows", "linux"],
    "architecture": "x64",
    "source_preferences": [],
}

_TORZNAB_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"
     xmlns:newznab="http://www.newznab.com/DTD/2010/feeds/attributes/">
  <channel>
    <item>
      <title>LibreOffice_7.6.4_Win_x86-64.torrent</title>
      <link>https://torrent.example.com/download/abc123</link>
      <pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate>
      <enclosure url="https://torrent.example.com/download/abc123" length="80000000" type="application/x-bittorrent"/>
      <newznab:attr name="size" value="80000000"/>
    </item>
    <item>
      <title>SomeOtherSoftware_1.0.exe</title>
      <link>https://torrent.example.com/download/other</link>
      <pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate>
    </item>
    <item>
      <title>LibreOffice_7.6.4_Win_x86-64.vol000+39.par2</title>
      <link>https://torrent.example.com/download/par2</link>
      <pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate>
    </item>
  </channel>
</rss>"""

_MAGNET_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom"
     xmlns:newznab="http://www.newznab.com/DTD/2010/feeds/attributes/">
  <channel>
    <item>
      <title>LibreOffice_7.6.4_Linux.torrent</title>
      <link>magnet:?xt=urn:btih:deadbeef&amp;dn=LibreOffice</link>
      <pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate>
      <newznab:attr name="size" value="90000000"/>
    </item>
  </channel>
</rss>"""


class TestTorznabAdapter:
    def _adapter(self):
        return TorznabAdapter(indexers=[], ini=None)

    def _indexer(self):
        return UsenetIndexerConfig(
            name="TestTorznab",
            url="https://torznab.example.com",
            api_key="testkey",
        )

    def test_source_type_is_torznab(self):
        adapter = self._adapter()
        assert adapter.source_type == "torznab"

    def test_empty_indexers_returns_empty(self):
        import asyncio

        adapter = self._adapter()
        results = asyncio.run(adapter.search_releases(SOFTWARE))
        assert results == []

    def test_parse_torznab_returns_torrent_url(self):
        adapter = self._adapter()
        indexer = self._indexer()
        results = adapter._parse_torznab_response(_TORZNAB_XML, indexer, SOFTWARE)
        assert len(results) == 1
        result = results[0]
        assert "torrent.example.com" in result.source_origin
        assert result.source_type == "torznab"

    def test_parse_torznab_filters_segment_files(self):
        """PAR2/segment files should be excluded."""
        adapter = self._adapter()
        indexer = self._indexer()
        results = adapter._parse_torznab_response(_TORZNAB_XML, indexer, SOFTWARE)
        # The par2 item should not be in results
        urls = [r.source_origin for r in results]
        assert not any("par2" in url for url in urls)

    def test_parse_torznab_filters_unrelated_items(self):
        """Items not fuzzy-matching the software name should be excluded."""
        adapter = self._adapter()
        indexer = self._indexer()
        results = adapter._parse_torznab_response(_TORZNAB_XML, indexer, SOFTWARE)
        names = [r.name for r in results]
        assert not any("SomeOther" in name for name in names)

    def test_parse_torznab_magnet_link(self):
        """Magnet links in the <link> element should be returned as source_origin."""
        adapter = self._adapter()
        indexer = self._indexer()
        results = adapter._parse_torznab_response(_MAGNET_XML, indexer, SOFTWARE)
        assert len(results) == 1
        assert results[0].source_origin.startswith("magnet:")

    def test_fetch_release_details_returns_torrent_type(self):
        import asyncio

        adapter = self._adapter()
        details = asyncio.run(
            adapter.fetch_release_details("https://example.com/file.torrent")
        )
        assert details["type"] == "torrent"
        assert details["handoff"] == "qbittorrent"

    def test_parse_torznab_version_extraction(self):
        adapter = self._adapter()
        indexer = self._indexer()
        results = adapter._parse_torznab_response(_TORZNAB_XML, indexer, SOFTWARE)
        assert results[0].version == "7.6.4"
