#!/usr/bin/env python3
"""Tests for cover_art.py — Cover Art Archive integration."""
import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open


class TestFetchCoverArt(unittest.TestCase):
    """Test fetch_cover_art function."""

    def test_empty_release_id(self):
        from otterrip_core.cover_art import fetch_cover_art
        result = fetch_cover_art("")
        self.assertIsNone(result)

    def test_invalid_release_id(self):
        from otterrip_core.cover_art import fetch_cover_art
        # A non-existent release will return 404
        result = fetch_cover_art("00000000-0000-0000-0000-000000000000", timeout=2)
        self.assertIsNone(result)

    def test_unknown_size_defaults_to_250(self):
        from otterrip_core.cover_art import fetch_cover_art
        # Should not crash, just warn and use 250
        result = fetch_cover_art("00000000-0000-0000-0000-000000000000", size="invalid", timeout=2)
        self.assertIsNone(result)

    def test_coverart_base_url(self):
        from otterrip_core.cover_art import COVERART_BASE
        self.assertEqual(COVERART_BASE, "https://coverartarchive.org/release/")

    def test_coverart_sizes(self):
        from otterrip_core.cover_art import COVERART_SIZES
        self.assertIn("thumbnail", COVERART_SIZES)
        self.assertIn("250", COVERART_SIZES)
        self.assertIn("500", COVERART_SIZES)
        self.assertIn("archive", COVERART_SIZES)


class TestCacheCoverArt(unittest.TestCase):
    """Test cache_cover_art and get_cached_cover functions."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_dir = os.path.join(self.tmpdir, "state")
        os.makedirs(self.state_dir, exist_ok=True)
        os.environ["OTTERRIP_STATE_DIR"] = self.state_dir

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OTTERRIP_STATE_DIR", None)

    def test_cache_and_retrieve(self):
        from otterrip_core.cover_art import cache_cover_art, get_cached_cover

        fake_jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 100  # Fake JPEG header
        release_id = "test-release-123"

        result = cache_cover_art(release_id, fake_jpeg)
        self.assertTrue(result)

        retrieved = get_cached_cover(release_id)
        self.assertIsNotNone(retrieved)
        self.assertEqual(retrieved, fake_jpeg)

    def test_get_cached_cover_missing(self):
        from otterrip_core.cover_art import get_cached_cover
        result = get_cached_cover("nonexistent-release")
        self.assertIsNone(result)

    def test_cache_no_state_dir(self):
        from otterrip_core.cover_art import cache_cover_art
        os.environ.pop("OTTERRIP_STATE_DIR", None)
        result = cache_cover_art("test", b"data")
        self.assertFalse(result)

    def test_get_cached_no_state_dir(self):
        from otterrip_core.cover_art import get_cached_cover
        os.environ.pop("OTTERRIP_STATE_DIR", None)
        result = get_cached_cover("test")
        self.assertIsNone(result)

    def test_cache_empty_data(self):
        from otterrip_core.cover_art import cache_cover_art, get_cached_cover
        release_id = "test-empty"
        cache_cover_art(release_id, b"")
        # Empty data should still be cached but retrieved as empty
        retrieved = get_cached_cover(release_id)
        self.assertEqual(retrieved, b"")


class TestEmbedCover(unittest.TestCase):
    """Test embed_cover function."""

    def test_missing_file(self):
        from otterrip_core.cover_art import embed_cover
        result = embed_cover("/nonexistent/file.mp3", b"fake cover")
        self.assertFalse(result)

    def test_empty_cover_data(self):
        from otterrip_core.cover_art import embed_cover
        result = embed_cover("/some/file.mp3", b"")
        self.assertFalse(result)

    def test_unsupported_format(self):
        from otterrip_core.cover_art import embed_cover
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(b"RIFF" + b"\x00" * 100)
            path = f.name
        try:
            result = embed_cover(path, b"fake cover")
            self.assertFalse(result)
        finally:
            os.unlink(path)


class TestFetchAndCacheCover(unittest.TestCase):
    """Test fetch_and_cache_cover convenience function."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_dir = os.path.join(self.tmpdir, "state")
        os.makedirs(self.state_dir, exist_ok=True)
        os.environ["OTTERRIP_STATE_DIR"] = self.state_dir

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        os.environ.pop("OTTERRIP_STATE_DIR", None)

    def test_returns_none_for_missing_release(self):
        from otterrip_core.cover_art import fetch_and_cache_cover
        result = fetch_and_cache_cover("00000000-0000-0000-0000-000000000000", timeout=2)
        self.assertIsNone(result)

    def test_empty_release_id(self):
        from otterrip_core.cover_art import fetch_and_cache_cover
        result = fetch_and_cache_cover("")
        self.assertIsNone(result)

    def test_cached_result_returned(self):
        """If cover is already cached, return it without network call."""
        from otterrip_core.cover_art import cache_cover_art, fetch_and_cache_cover

        fake_jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 100
        release_id = "cached-release-123"
        cache_cover_art(release_id, fake_jpeg)

        result = fetch_and_cache_cover(release_id)
        self.assertEqual(result, fake_jpeg)


class TestMakePicture(unittest.TestCase):
    """Test _make_picture helper."""

    def test_jpeg_picture(self):
        from otterrip_core.cover_art import _make_picture
        data = b"\xff\xd8\xff\xe0" + b"\x00" * 100
        pic = _make_picture(data)
        self.assertEqual(pic.mime, "image/jpeg")
        self.assertEqual(pic.type, 3)
        self.assertEqual(pic.desc, "Cover")

    def test_png_picture(self):
        from otterrip_core.cover_art import _make_picture
        data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        pic = _make_picture(data)
        self.assertEqual(pic.mime, "image/png")


if __name__ == "__main__":
    unittest.main()
