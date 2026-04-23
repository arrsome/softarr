"""Unit tests for UsenetAdapter parsing, deduplication, and fuzzy matching.

Tests use real title strings taken from the libreofficedrunkenslugresponse.xml
fixture to verify the adapter behaves correctly against real-world data.
"""

from softarr.adapters.usenet import (
    _SEGMENT_PATTERNS,
    UsenetAdapter,
    UsenetIndexerConfig,
)

# ---------------------------------------------------------------------------
# _clean_title
# ---------------------------------------------------------------------------


class TestCleanTitle:
    def test_quoted_filename_extracted(self):
        title = 'Testen voor de gein (speed) 20 21- "LibreOffice_26.2.1_Win_x86-64.vol426+38.PAR2"'
        # Segment patterns filter these out before clean is called, but test clean directly
        result = UsenetAdapter._clean_title(title)
        # Should extract the quoted content and strip extension
        assert "LibreOffice" in result

    def test_quoted_nzb_filename_stripped(self):
        title = 'NFO file zie- "LibreOffice_26.2.1_Win_x86-64.sfv"'
        result = UsenetAdapter._clean_title(title)
        assert "LibreOffice_26.2.1_Win_x86-64" in result
        assert ".sfv" not in result

    def test_segment_prefix_stripped(self):
        title = '[04/14] - "Portable LibreOffice 25.8.4.2.part2.rar"'
        result = UsenetAdapter._clean_title(title)
        # The quoted filename should be extracted
        assert "Portable LibreOffice 25.8.4.2" in result

    def test_numbered_prefix_stripped(self):
        title = '(01/21) - Description - "LibreOffice.7.6.2.MacOS.x86-64.dmg x64-CRACKFIX-MAC-APP-GP.par2"'
        result = UsenetAdapter._clean_title(title)
        assert result  # Should return something non-empty

    def test_plain_title_unchanged(self):
        title = "LibreOffice Pro 2025 v25.8.1 Win_x64-86 Multilingual"
        result = UsenetAdapter._clean_title(title)
        # No quoted content, no prefixes -- should return the cleaned title
        assert "LibreOffice" in result

    def test_size_annotation_stripped(self):
        title = (
            '(01/21) - Description - "LibreOffice.7.6.2.MacOS.x86-64.dmg" - 385,85 MB'
        )
        result = UsenetAdapter._clean_title(title)
        assert "MB" not in result
        assert "385" not in result


# ---------------------------------------------------------------------------
# _extract_version
# ---------------------------------------------------------------------------


class TestExtractVersion:
    def test_underscore_delimited(self):
        assert (
            UsenetAdapter._extract_version("LibreOffice_26.2.1_Win_x86-64") == "26.2.1"
        )

    def test_v_prefix(self):
        assert UsenetAdapter._extract_version("AppName v1.2.3 Win") == "1.2.3"

    def test_four_components(self):
        assert (
            UsenetAdapter._extract_version("Portable LibreOffice 25.8.4.2.part2.rar")
            == "25.8.4.2"
        )

    def test_prefers_longer_version(self):
        # Title has "2025" (year-like) and "25.8.1" (version) -- prefers more components
        title = "LibreOffice Pro 2025 v25.8.1 Win_x64-86 Multilingual"
        result = UsenetAdapter._extract_version(title)
        assert result == "25.8.1"

    def test_no_version_returns_unknown(self):
        assert UsenetAdapter._extract_version("Just a random title") == "unknown"

    def test_dot_delimited_in_filename(self):
        title = '(1/9) - Description - "LibreOffice.7.6.1.MacOS.x86-64.dmg"'
        result = UsenetAdapter._extract_version(title)
        assert result == "7.6.1"


# ---------------------------------------------------------------------------
# _fuzzy_matches
# ---------------------------------------------------------------------------


class TestFuzzyMatches:
    def test_exact_substring_match(self):
        assert (
            UsenetAdapter._fuzzy_matches(
                "LibreOffice", "LibreOffice_26.2.1_Win_x86-64", []
            )
            is True
        )

    def test_case_insensitive_match(self):
        assert (
            UsenetAdapter._fuzzy_matches("libreoffice", "Testen LibreOffice_26.2.1", [])
            is True
        )

    def test_alias_substring_match(self):
        # Alias appears in the title
        assert (
            UsenetAdapter._fuzzy_matches(
                "VLC", "LibreOffice_26.2.1 LibreOffice", ["LibreOffice"]
            )
            is True
        )

    def test_no_match(self):
        assert (
            UsenetAdapter._fuzzy_matches("VLC", "LibreOffice_26.2.1_Win_x86-64", [])
            is False
        )

    def test_empty_canonical_with_matching_alias(self):
        assert (
            UsenetAdapter._fuzzy_matches("", "LibreOffice_26.2.1", ["LibreOffice"])
            is True
        )

    def test_empty_canonical_no_aliases_no_match(self):
        assert UsenetAdapter._fuzzy_matches("", "LibreOffice_26.2.1", []) is False


# ---------------------------------------------------------------------------
# Segment pattern detection
# ---------------------------------------------------------------------------


class TestSegmentPatterns:
    def test_vol_par2_detected(self):
        title = 'Testen voor de gein (speed) 20 21- "LibreOffice_26.2.1_Win_x86-64.vol426+38.PAR2"'
        assert _SEGMENT_PATTERNS.search(title) is not None

    def test_sfv_detected(self):
        title = 'NFO file zie- "LibreOffice_26.2.1_Win_x86-64.sfv"'
        assert _SEGMENT_PATTERNS.search(title) is not None

    def test_part_rar_detected(self):
        title = '[04/14] - "Portable LibreOffice 25.8.4.2.part2.rar"'
        assert _SEGMENT_PATTERNS.search(title) is not None

    def test_clean_title_not_detected(self):
        title = "LibreOffice Pro 2025 v25.8.1 Win_x64-86 Multilingual"
        assert _SEGMENT_PATTERNS.search(title) is None

    def test_nzb_title_not_detected(self):
        # A plain NZB post title with no par2/sfv/rar segment markers
        title = "(1/9) - Description - LibreOffice.7.6.1.MacOS.x86-64.dmg"
        assert _SEGMENT_PATTERNS.search(title) is None


# ---------------------------------------------------------------------------
# _infer_publisher
# ---------------------------------------------------------------------------


class TestInferPublisher:
    def test_publisher_found_in_title(self):
        result = UsenetAdapter._infer_publisher(
            "LibreOffice by The Document Foundation v26.2.1",
            "The Document Foundation",
        )
        assert result == "The Document Foundation"

    def test_publisher_not_in_title(self):
        result = UsenetAdapter._infer_publisher(
            "LibreOffice Pro 2025 v25.8.1 Win_x64-86",
            "The Document Foundation",
        )
        assert result is None

    def test_no_expected_publisher(self):
        result = UsenetAdapter._infer_publisher(
            "LibreOffice Pro 2025 v25.8.1 Win_x64-86",
            None,
        )
        assert result is None


# ---------------------------------------------------------------------------
# Full parse pipeline -- deduplication and filtering
# ---------------------------------------------------------------------------


class TestParseNewznabResponse:
    """Tests for _parse_newznab_response using synthetic XML."""

    def _make_item(
        self, title: str, size: int = 1000000, nzb_id: str = "abc123"
    ) -> str:
        return f"""
        <item>
          <title>{title}</title>
          <link>https://drunkenslug.com/getnzb/{nzb_id}.nzb</link>
          <pubDate>Tue, 17 Mar 2026 12:29:01 +0000</pubDate>
          <enclosure url="https://drunkenslug.com/getnzb/{nzb_id}.nzb" length="{size}" type="application/x-nzb"/>
          <newznab:attr name="category" value="4010"/>
          <newznab:attr name="size" value="{size}"/>
        </item>"""

    def _make_xml(self, items: str) -> str:
        return f"""<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"
             xmlns:atom="http://www.w3.org/2005/Atom"
             xmlns:newznab="http://www.newznab.com/DTD/2010/feeds/attributes/"
             encoding="utf-8">
          <channel>
            <newznab:response offset="0" total="5"/>
            {items}
          </channel>
        </rss>"""

    def _indexer(self):
        return UsenetIndexerConfig(
            name="TestIndexer",
            url="https://example.com",
            api_key="testkey",
        )

    def _software(self):
        return {
            "canonical_name": "LibreOffice",
            "aliases": [],
            "expected_publisher": None,
            "supported_os": ["windows"],
            "architecture": "x64",
        }

    def test_par2_segments_excluded(self):
        """PAR2 recovery block items should not appear in results."""
        adapter = UsenetAdapter()
        xml = self._make_xml(
            self._make_item(
                'Testen voor 20 21- "LibreOffice_26.2.1_Win_x86-64.vol426+38.PAR2"',
                nzb_id="aaa",
            )
            + self._make_item(
                'Testen voor 19 21- "LibreOffice_26.2.1_Win_x86-64.vol388+38.PAR2"',
                nzb_id="bbb",
            )
            + self._make_item(
                "LibreOffice Pro 2025 v25.8.1 Win_x64-86 Multilingual",
                size=500000000,
                nzb_id="ccc",
            )
        )
        results = adapter._parse_newznab_response(
            xml, self._indexer(), self._software()
        )
        # Only the non-PAR2 item should remain
        assert len(results) == 1
        assert results[0].version == "25.8.1"

    def test_deduplication_keeps_largest(self):
        """When two items have the same display_name+version, keep the larger one."""
        adapter = UsenetAdapter()
        xml = self._make_xml(
            self._make_item(
                "LibreOffice Pro 2025 v25.8.1 Win_x64-86 Multilingual",
                size=100,
                nzb_id="small",
            )
            + self._make_item(
                "LibreOffice Pro 2025 v25.8.1 Win_x64-86 Multilingual",
                size=500000000,
                nzb_id="large",
            )
        )
        results = adapter._parse_newznab_response(
            xml, self._indexer(), self._software()
        )
        assert len(results) == 1
        assert "large" in results[0].source_origin

    def test_unrelated_results_filtered(self):
        """Items with no fuzzy match to canonical_name should be excluded."""
        adapter = UsenetAdapter()
        xml = self._make_xml(
            self._make_item(
                "VLC Media Player 3.0.20 Win64", size=50000000, nzb_id="vlc"
            )
            + self._make_item(
                "LibreOffice Pro 2025 v25.8.1 Win_x64-86", size=500000000, nzb_id="lo"
            )
        )
        results = adapter._parse_newznab_response(
            xml, self._indexer(), self._software()
        )
        # VLC should be filtered out
        assert len(results) == 1
        assert "25.8.1" in results[0].version

    def test_display_name_set(self):
        """Results should have a display_name distinct from the raw title."""
        adapter = UsenetAdapter()
        xml = self._make_xml(
            self._make_item(
                "LibreOffice Pro 2025 v25.8.1 Win_x64-86 Multilingual",
                size=500000000,
                nzb_id="lo",
            )
        )
        results = adapter._parse_newznab_response(
            xml, self._indexer(), self._software()
        )
        assert len(results) == 1
        assert results[0].display_name is not None

    def test_nzb_url_whitespace_stripped(self):
        """NZB URLs containing trailing whitespace should be stripped."""
        adapter = UsenetAdapter()
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"
             xmlns:newznab="http://www.newznab.com/DTD/2010/feeds/attributes/">
          <channel>
            <item>
              <title>LibreOffice Pro 2025 v25.8.1 Win_x64-86 Multilingual</title>
              <link>https://example.com/getnzb/abc.nzb&amp;i=1&amp;r=key </link>
              <pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate>
              <enclosure url="https://example.com/getnzb/abc.nzb&amp;i=1&amp;r=key " length="500000000" type="application/x-nzb"/>
              <newznab:attr name="size" value="500000000"/>
            </item>
          </channel>
        </rss>"""
        results = adapter._parse_newznab_response(
            xml, self._indexer(), self._software()
        )
        assert len(results) == 1
        assert not results[0].source_origin.endswith(" ")

    def test_extended_grabs_in_raw_data(self):
        """When extended=1 is requested, the grabs attribute should appear in raw_data."""
        adapter = UsenetAdapter()
        xml = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0"
             xmlns:newznab="http://www.newznab.com/DTD/2010/feeds/attributes/">
          <channel>
            <item>
              <title>LibreOffice Pro 2025 v25.8.1 Win_x64-86 Multilingual</title>
              <link>https://example.com/getnzb/abc.nzb</link>
              <pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate>
              <enclosure url="https://example.com/getnzb/abc.nzb" length="500000000" type="application/x-nzb"/>
              <newznab:attr name="category" value="4010"/>
              <newznab:attr name="size" value="500000000"/>
              <newznab:attr name="grabs" value="42"/>
              <newznab:attr name="poster" value="test@example.com"/>
              <newznab:attr name="group" value="alt.binaries.test"/>
            </item>
          </channel>
        </rss>"""
        results = adapter._parse_newznab_response(
            xml, self._indexer(), self._software()
        )
        assert len(results) == 1
        raw = results[0].raw_data
        assert raw.get("grabs") == "42"
        assert raw.get("poster") == "test@example.com"
        assert raw.get("group") == "alt.binaries.test"
