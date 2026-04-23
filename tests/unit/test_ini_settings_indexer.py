"""Tests for IniSettingsManager indexer health stats (IDX-01)."""

import os
import tempfile


def make_ini(indexer_name=None):
    """Create an IniSettingsManager backed by a temp file.

    If indexer_name is given, a pre-created indexer section is written.
    """
    from softarr.core.ini_settings import _INDEXER_PREFIX, IniSettingsManager

    fd, path = tempfile.mkstemp(suffix=".ini")
    os.close(fd)
    content = "[softarr]\nSECRET_KEY = test-secret\n"
    if indexer_name:
        section = f"{_INDEXER_PREFIX}{indexer_name}"
        content += f"\n[{section}]\nurl = https://example.com\napi_key = testkey\nenabled = true\npriority = 0\n"
    with open(path, "w") as f:
        f.write(content)
    return IniSettingsManager(path), path


def test_record_indexer_result_success():
    """record_indexer_result should increment success_count on success."""
    ini, path = make_ini(indexer_name="TestIndexer")
    try:
        ini.record_indexer_result("TestIndexer", True, 250)
        stats = ini.get_indexer_stats("TestIndexer")
        assert stats["success_count"] >= 1
        assert stats["last_response_ms"] == 250
        assert stats["last_success_at"] is not None
    finally:
        os.unlink(path)


def test_record_indexer_result_failure():
    """record_indexer_result should increment failure_count on failure."""
    ini, path = make_ini(indexer_name="TestIndexer")
    try:
        ini.record_indexer_result("TestIndexer", False, 5000)
        stats = ini.get_indexer_stats("TestIndexer")
        assert stats["failure_count"] >= 1
        assert stats["last_failure_at"] is not None
    finally:
        os.unlink(path)


def test_indexer_stats_accumulate():
    """Multiple record_indexer_result calls should accumulate counts."""
    ini, path = make_ini(indexer_name="MyIndex")
    try:
        ini.record_indexer_result("MyIndex", True, 100)
        ini.record_indexer_result("MyIndex", True, 200)
        ini.record_indexer_result("MyIndex", False, 5000)

        stats = ini.get_indexer_stats("MyIndex")
        assert stats["success_count"] == 2
        assert stats["failure_count"] == 1
    finally:
        os.unlink(path)


def test_get_indexer_stats_unknown():
    """get_indexer_stats for unknown indexer should return zero counts."""
    ini, path = make_ini()
    try:
        stats = ini.get_indexer_stats("NonExistent")
        assert stats["success_count"] == 0
        assert stats["failure_count"] == 0
        assert stats["last_success_at"] is None
        assert stats["last_failure_at"] is None
    finally:
        os.unlink(path)
