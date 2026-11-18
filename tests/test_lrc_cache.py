#!/usr/bin/env python3
"""Unit tests for otterrip_core.lrc_cache module (SQLite backend)."""
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

# Ensure lib is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from otterrip_core.lrc_cache import LyricsCache, _cache_key
from otterrip_core.state_db import StateDB


class TestCacheKey(unittest.TestCase):
    """Test the _cache_key helper function."""

    def test_basic_key(self):
        key = _cache_key("Artist", "Title", 180000)
        self.assertEqual(len(key), 64)  # SHA-256 hex digest

    def test_key_is_case_insensitive(self):
        key1 = _cache_key("Artist", "Title", 180000)
        key2 = _cache_key("artist", "title", 180000)
        self.assertEqual(key1, key2)

    def test_key_includes_isrc(self):
        key1 = _cache_key("Artist", "Title", 180000, isrc="USRC12345678")
        key2 = _cache_key("Artist", "Title", 180000)
        self.assertNotEqual(key1, key2)

    def test_key_includes_fingerprint(self):
        key1 = _cache_key("Artist", "Title", 180000, fingerprint="abc123")
        key2 = _cache_key("Artist", "Title", 180000)
        self.assertNotEqual(key1, key2)

    def test_key_strips_whitespace(self):
        key1 = _cache_key("  Artist  ", "  Title  ", 180000)
        key2 = _cache_key("Artist", "Title", 180000)
        self.assertEqual(key1, key2)


class TestLyricsCache(unittest.TestCase):
    """Test LyricsCache class with SQLite backend."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        os.environ["OTTERRIP_STATE_DIR"] = self.tmpdir
        self.cache = LyricsCache(self.tmpdir, ttl_seconds=604800)
        self.db = StateDB(self.tmpdir)

    def tearDown(self):
        self.cache.clear()
        if "OTTERRIP_STATE_DIR" in os.environ:
            del os.environ["OTTERRIP_STATE_DIR"]
        self.db.close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_set_and_get(self):
        bundle = {"synced": "[00:00.000] Hello", "provider": "lrclib"}
        self.cache.set("Artist", "Title", bundle, duration_ms=180000)
        result = self.cache.get("Artist", "Title", duration_ms=180000)
        self.assertIsNotNone(result)
        self.assertEqual(result["synced"], "[00:00.000] Hello")

    def test_get_missing_returns_none(self):
        result = self.cache.get("Nobody", "Nothing")
        self.assertIsNone(result)

    def test_ttl_expiration(self):
        """Entries older than TTL should not be returned."""
        bundle = {"text": "old lyrics"}
        self.cache.set("Artist", "Title", bundle, duration_ms=180000)
        # Manually expire the entry
        key = _cache_key("Artist", "Title", 180000)
        self.db._conn.execute(
            "UPDATE lyrics_cache SET cached_at = ? WHERE key = ?",
            (time.time() - 999999, key),
        )
        self.db._conn.commit()
        result = self.cache.get("Artist", "Title", duration_ms=180000)
        self.assertIsNone(result)

    def test_time_persistence(self):
        """cached_at should use time.time() which persists across process restarts."""
        bundle = {"text": "persistent lyrics"}
        self.cache.set("Artist", "Title", bundle, duration_ms=180000)
        key = _cache_key("Artist", "Title", 180000)
        row = self.db._conn.execute(
            "SELECT cached_at FROM lyrics_cache WHERE key = ?", (key,)
        ).fetchone()
        cached_at = row["cached_at"]
        now = time.time()
        self.assertAlmostEqual(cached_at, now, delta=5)

    def test_get_with_isrc(self):
        bundle = {"synced": "[00:00.000] ISRC lyrics"}
        self.cache.set("Artist", "Title", bundle, duration_ms=180000, isrc="USRC12345678")
        result = self.cache.get("Artist", "Title", duration_ms=180000, isrc="USRC12345678")
        self.assertIsNotNone(result)

    def test_get_with_fingerprint(self):
        bundle = {"synced": "[00:00.000] fingerprint lyrics"}
        self.cache.set("Artist", "Title", bundle, duration_ms=180000, fingerprint="fp123")
        result = self.cache.get("Artist", "Title", duration_ms=180000, fingerprint="fp123")
        self.assertIsNotNone(result)

    def test_clear(self):
        self.cache.set("Artist1", "Title1", {"text": "a"}, duration_ms=100)
        self.cache.set("Artist2", "Title2", {"text": "b"}, duration_ms=200)
        count = self.cache.clear()
        self.assertEqual(count, 2)
        self.assertIsNone(self.cache.get("Artist1", "Title1", duration_ms=100))
        self.assertIsNone(self.cache.get("Artist2", "Title2", duration_ms=200))

    def test_ttl_short_expiration(self):
        """Test with a very short TTL to verify expiration logic."""
        short_cache = LyricsCache(self.tmpdir, ttl_seconds=1)
        bundle = {"text": "expires instantly"}
        short_cache.set("Artist", "Title", bundle, duration_ms=180000)
        # Manually expire
        key = _cache_key("Artist", "Title", 180000)
        self.db._conn.execute(
            "UPDATE lyrics_cache SET cached_at = ? WHERE key = ?",
            (time.time() - 10, key),
        )
        self.db._conn.commit()
        result = short_cache.get("Artist", "Title", duration_ms=180000)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
