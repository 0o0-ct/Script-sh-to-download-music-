#!/usr/bin/env python3
"""Unit tests for otterrip_core.lrc_cache module."""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

# Ensure lib is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from otterrip_core.lrc_cache import LyricsCache, _cache_key


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
    """Test LyricsCache class."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.cache = LyricsCache(self.tmpdir, ttl_seconds=604800)

    def tearDown(self):
        self.cache.clear()

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
        key = _cache_key("Artist", "Title", 180000)
        path = self.cache._path(key)
        # Write an entry with a very old timestamp
        old_time = time.time() - 999999  # far in the past
        payload = {"cached_at": old_time, "bundle": bundle, "keyed_by": "legacy"}
        path.write_text(json.dumps(payload), encoding="utf-8")
        # With default TTL (604800 seconds), this should be expired
        result = self.cache.get("Artist", "Title", duration_ms=180000)
        self.assertIsNone(result)
        # The expired file should be removed
        self.assertFalse(path.exists())

    def test_time_persistence(self):
        """cached_at should use time.time() which persists across process restarts."""
        bundle = {"text": "persistent lyrics"}
        self.cache.set("Artist", "Title", bundle, duration_ms=180000)
        key = _cache_key("Artist", "Title", 180000)
        path = self.cache._path(key)
        data = json.loads(path.read_text(encoding="utf-8"))
        cached_at = data["cached_at"]
        # cached_at should be close to current wall-clock time, not CPU time
        now = time.time()
        self.assertAlmostEqual(cached_at, now, delta=5)  # within 5 seconds

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

    def test_corrupt_entry_returns_none(self):
        """A corrupt JSON file should not crash the cache."""
        key = _cache_key("Artist", "Title", 180000)
        path = self.cache._path(key)
        path.write_text("not valid json {{{", encoding="utf-8")
        result = self.cache.get("Artist", "Title", duration_ms=180000)
        self.assertIsNone(result)

    def test_ttl_short_expiration(self):
        """Test with a very short TTL to verify expiration logic."""
        short_cache = LyricsCache(self.tmpdir + "/short", ttl_seconds=0)
        short_cache.cache_dir.mkdir(parents=True, exist_ok=True)
        bundle = {"text": "expires instantly"}
        short_cache.set("Artist", "Title", bundle, duration_ms=180000)
        # With TTL=0, any entry should be expired immediately
        result = short_cache.get("Artist", "Title", duration_ms=180000)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
