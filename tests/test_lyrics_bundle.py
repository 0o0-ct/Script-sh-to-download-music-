#!/usr/bin/env python3
"""Tests for the new lyrics pipeline modules.

Pure, deterministic, no network required.
"""
import unittest
from pathlib import Path
from unittest.mock import patch

from otterrip_core.lyrics_bundle import LyricsBundle, bundle_from_legacy
from otterrip_core.lrc_validator import (
    validate_and_normalise_lrc,
    normalise_lrc,
    parse_lrc,
    shift_lrc,
    LrcValidationResult,
)
from otterrip_core.query_clean import (
    clean_title,
    clean_artist,
    split_feat,
    build_query_terms,
    normalize_unicode,
)
from otterrip_core.offset import apply_offset_to_bundle, compute_offset_from_anchor
from otterrip_core.lrc_cache import LyricsCache


class TestLyricsBundle(unittest.TestCase):
    """Test the LyricsBundle dataclass."""

    def test_word_kind(self):
        b = LyricsBundle(kind="word", lines=[(1000, "word1")], confidence=0.9)
        self.assertTrue(b.has_sync)
        self.assertFalse(b.is_plain)
        self.assertEqual(b.kind, "word")
        self.assertEqual(b.lines, [(1000, "word1")])

    def test_line_kind(self):
        b = LyricsBundle(kind="line", lines=[(2000, "line1")], confidence=0.95)
        self.assertTrue(b.has_sync)
        self.assertEqual(b.kind, "line")

    def test_plain_kind(self):
        b = LyricsBundle(kind="plain", plain="hello world", confidence=0.5)
        self.assertFalse(b.has_sync)
        self.assertTrue(b.is_plain)

    def test_instrumental_kind(self):
        b = LyricsBundle(kind="instrumental", confidence=1.0)
        self.assertTrue(b.is_instrumental)

    def test_none_kind(self):
        b = LyricsBundle(kind="none")
        self.assertTrue(b.is_none)

    def test_duration_delta(self):
        b = LyricsBundle(
            kind="line", lines=[(1000, "x")],
            audio_duration_ms=180000, ref_duration_ms=183000,
        )
        self.assertEqual(b.duration_delta_ms(), 3000)

    def test_to_legacy_result(self):
        b = LyricsBundle(
            kind="line", lines=[(1000, "test")],
            confidence=0.9, source="lrclib",
        )
        d = b.to_legacy_result("test")
        self.assertEqual(d["provider"], "test")
        self.assertEqual(d["sync_level"], 1)
        self.assertEqual(d["timed_entries"], [(1.0, "test")])

    def test_bundle_from_legacy(self):
        r = bundle_from_legacy(
            provider="test", title="Song", artist="Artist", album="Alb",
            duration_s=180.0, sync_level=1,
            plain_text="plain", timed_entries=[(1.0, "line1")],
            confidence=0.8, source="lrclib",
        )
        self.assertEqual(r.kind, "line")
        self.assertEqual(r.title, "Song")
        self.assertEqual(r.artist, "Artist")
        self.assertEqual(r.lines, [(1000, "line1")])
        self.assertEqual(r.confidence, 0.8)


class TestLrcValidator(unittest.TestCase):
    """Test LRC parsing, validation, and normalisation."""

    def test_parse_single_timestamp(self):
        entries = parse_lrc("[00:01.500] Hello world")
        self.assertEqual(entries, [(1500, "Hello world")])

    def test_parse_multi_timestamp(self):
        lrc = "[00:01.000][00:02.500] line one\n[00:03.000] second line"
        entries = parse_lrc(lrc)
        # Multi-tag creates a separate entry for each timestamp.
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0], (1000, "line one"))
        self.assertEqual(entries[1], (2500, "line one"))
        self.assertEqual(entries[2], (3000, "second line"))

    def test_parse_different_ms_precision(self):
        # Multi-tag: same text, multiple timestamps.
        # [00:01.000] = 1 second = 1000ms, [00:02.500] = 2s + 500ms = 2500ms
        entries = parse_lrc("[00:01.000] text [00:02.500] more")
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0], (1000, "text  more"))
        self.assertEqual(entries[1], (2500, "text  more"))

    def test_parse_headers_ignored(self):
        entries = parse_lrc("[ti: Song]\n[00:01.000] line")
        self.assertEqual(entries, [(1000, "line")])

    def test_normalise_lrc(self):
        entries = [(1000, "a"), (2000, "b")]
        lrc = normalise_lrc(entries)
        self.assertIn("[00:01.00]", lrc)
        self.assertIn("[00:02.00]", lrc)

    def test_shift_lrc_positive(self):
        lrc = "[00:01.00] line1\n[00:02.00] line2"
        shifted = shift_lrc(lrc, 500)
        self.assertIn("[00:01.50]", shifted)
        self.assertIn("[00:02.50]", shifted)

    def test_shift_lrc_negative(self):
        lrc = "[00:01.00] line1\n[00:02.00] line2"
        shifted = shift_lrc(lrc, -300)
        self.assertIn("[00:00.70]", shifted)
        self.assertIn("[00:01.70]", shifted)

    def test_shift_negative_clamps_to_zero(self):
        lrc = "[00:00.50] line1"
        shifted = shift_lrc(lrc, -1000)
        self.assertIn("[00:00.00]", shifted)

    def test_validate_valid_lrc(self):
        result = validate_and_normalise_lrc(
            "[00:01.00] line1\n[00:02.00] line2", audio_duration_s=30.0
        )
        self.assertTrue(result.valid)
        self.assertEqual(len(result.entries), 2)
        self.assertEqual(result.entries[0][1], "line1")

    def test_validate_ratio_below_60pct(self):
        lrc = "just one line\n[00:01.00] another"
        result = validate_and_normalise_lrc(lrc, audio_duration_s=30.0)
        self.assertFalse(result.valid)
        self.assertIn("mínimo 60%", result.reason)

    def test_validate_ad_line_removed(self):
        lrc = "[00:01.00] real line\n[00:02.00] advertisement here"
        result = validate_and_normalise_lrc(lrc, audio_duration_s=30.0)
        self.assertTrue(result.valid)
        self.assertEqual(len([e for e in result.entries if "ad" in e[1].lower()]), 0)

    def test_validate_duration_exceeded(self):
        # Last timestamp 5.0s, audio duration 3.0s, tolerance 3s
        # → 5.0 > 6.0 → 5.0 <= 6.0 so VALID (no exceed)
        # To exceed: use audio_duration_s=1.0 → 5.0 > 4.0 → invalid
        lrc = "[00:01.00] line1\n[00:02.00] line2\n[00:05.00] line3"
        result = validate_and_normalise_lrc(lrc, audio_duration_s=1.0)
        self.assertFalse(result.valid)
        self.assertIn("excede", result.reason)

    def test_validate_monotonic_corrects(self):
        lrc = "[00:02.00] first\n[00:01.00] second"
        result = validate_and_normalise_lrc(lrc, audio_duration_s=30.0)
        self.assertTrue(result.valid)
        self.assertLessEqual(result.entries[0][0], result.entries[1][0])


class TestQueryClean(unittest.TestCase):
    """Test query cleaning and cleaning logic."""

    def test_clean_title_removes_video(self):
        self.assertEqual(clean_title("Bohemian Rhapsody (Official Video)"), "Bohemian Rhapsody")
        self.assertEqual(clean_title("Shape of You [Lyrics]"), "Shape of You")
        self.assertEqual(clean_title("Blinding Lights (feat. Kendrick)"), "Blinding Lights")

    def test_clean_artist_removes_topic(self):
        self.assertEqual(clean_artist("ArtistName - Topic"), "ArtistName")

    def test_split_feat(self):
        title, feat = split_feat("Blinding Lights (feat. The Weeknd)")
        self.assertEqual(title, "Blinding Lights")
        self.assertEqual(feat, "The Weeknd")

    def test_build_query_terms(self):
        terms = build_query_terms(
            title="Song (Official Video)",
            artist="Artist - Topic",
            album="Album Name",
        )
        self.assertEqual(terms["title"], "Song")
        self.assertEqual(terms["artist"], "Artist")
        self.assertEqual(terms["album"], "Album Name")

    def test_normalize_unicode(self):
        self.assertEqual(
            normalize_unicode("café"), "cafe"
        )


class TestOffset(unittest.TestCase):
    """Test offset/shift helpers."""

    def test_compute_offset(self):
        self.assertEqual(compute_offset_from_anchor(1000, 1500), 500)
        self.assertEqual(compute_offset_from_anchor(1500, 1000), -500)

    def test_apply_offset(self):
        lrc = "[00:01.00] line1\n[00:02.00] line2"
        shifted = apply_offset_to_bundle(lrc, 500)
        self.assertIn("[00:01.50]", shifted)


class TestLrcCache(unittest.TestCase):
    """Test the local cache."""

    def test_get_set(self):
        import tempfile
        import os

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = LyricsCache(tmpdir)
            bundle = {
                "kind": "line", "lrc": "[00:01.00] line", "plain": None,
                "lines": [(1000, "line")], "source": "lrclib",
                "audio_duration_ms": 100000,
            }
            cache.set("Artist", "Title", bundle, duration_ms=100000)
            retrieved = cache.get("Artist", "Title", 100000)
            self.assertIsNotNone(retrieved)
            self.assertEqual(retrieved["kind"], "line")
