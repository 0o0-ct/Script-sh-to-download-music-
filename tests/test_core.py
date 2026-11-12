#!/usr/bin/env python3
"""Basic tests for otterrip_core modules."""
import unittest
import asyncio
import tempfile
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

from otterrip_core.models import LyricsResult, SyncLevel
from otterrip_core.adapters.base import SongQuery
from otterrip_core.adapters.lrclib import LrclibProvider, _parse_lrc
from otterrip_core.lyrics import select_best, _format_lrc


class TestModels(unittest.TestCase):
    """Test data models."""

    def test_sync_level_hierarchy(self):
        self.assertEqual(SyncLevel.NONE, 0)
        self.assertEqual(SyncLevel.LINES, 1)
        self.assertEqual(SyncLevel.WORDS, 2)
        self.assertEqual(SyncLevel.SYLLABLES, 3)

    def test_lyrics_result_plain(self):
        r = LyricsResult(provider="test", plain_text="hello world")
        self.assertTrue(r.is_plain)
        self.assertFalse(r.has_sync)

    def test_lyrics_result_synced(self):
        r = LyricsResult(
            provider="test",
            sync_level=SyncLevel.LINES,
            timed_entries=[(0.0, "line1"), (5.0, "line2")],
        )
        self.assertFalse(r.is_plain)
        self.assertTrue(r.has_sync)

    def test_score(self):
        r1 = LyricsResult(provider="a", sync_level=SyncLevel.LINES, confidence=0.8)
        r2 = LyricsResult(provider="b", sync_level=SyncLevel.WORDS, confidence=0.8)
        self.assertGreater(r2.score(), r1.score())


class TestLrclibAdapter(unittest.TestCase):
    """Test LRCLIB adapter utilities."""

    def test_parse_lrc(self):
        text = "[00:12.345] Hello world\n[01:02.678] Second line"
        entries = _parse_lrc(text)
        self.assertEqual(len(entries), 2)
        self.assertAlmostEqual(entries[0][0], 12.345, places=2)
        self.assertEqual(entries[0][1], "Hello world")
        self.assertAlmostEqual(entries[1][0], 62.678, places=2)

    def test_parse_lrc_empty(self):
        entries = _parse_lrc("no timestamps here")
        self.assertEqual(len(entries), 0)

    def test_fuzzy_match_exact(self):
        p = LrclibProvider()
        self.assertEqual(p._fuzzy_match("Hello", "Hello"), 1.0)

    def test_fuzzy_match_containment(self):
        p = LrclibProvider()
        self.assertAlmostEqual(p._fuzzy_match("Hello", "Hello World"), 0.8)

    def test_fuzzy_match_no_match(self):
        p = LrclibProvider()
        self.assertEqual(p._fuzzy_match("ABC", "XYZ"), 0.0)


class TestSelectBest(unittest.TestCase):
    """Test result selection logic."""

    def test_prefers_synced(self):
        results = [
            LyricsResult(provider="a", sync_level=SyncLevel.NONE, confidence=0.9, plain_text="text"),
            LyricsResult(provider="b", sync_level=SyncLevel.LINES, confidence=0.7, timed_entries=[(0, "x")]),
        ]
        best = select_best(results, SongQuery(title="t", artist="a"), prefer_synced=True)
        self.assertEqual(best.provider, "b")

    def test_prefers_higher_confidence(self):
        results = [
            LyricsResult(provider="a", sync_level=SyncLevel.LINES, confidence=0.5, timed_entries=[(0, "x")]),
            LyricsResult(provider="b", sync_level=SyncLevel.LINES, confidence=0.9, timed_entries=[(0, "y")]),
        ]
        best = select_best(results, SongQuery(title="t", artist="a"), prefer_synced=True)
        self.assertEqual(best.provider, "b")

    def test_rejects_low_confidence(self):
        results = [
            LyricsResult(provider="a", sync_level=SyncLevel.NONE, confidence=0.1, plain_text="wrong"),
        ]
        best = select_best(results, SongQuery(title="t", artist="a"), prefer_synced=False)
        # Should still return something when plain_ok
        self.assertIsNotNone(best)

    def test_no_results(self):
        best = select_best([], SongQuery(title="t", artist="a"))
        self.assertIsNone(best)

    def test_no_synced_when_preferred(self):
        results = [
            LyricsResult(provider="a", sync_level=SyncLevel.NONE, confidence=0.9, plain_text="text"),
        ]
        best = select_best(results, SongQuery(title="t", artist="a"), prefer_synced=True, plain_ok=False)
        self.assertIsNone(best)


class TestFormatLrc(unittest.TestCase):
    """Test LRC formatting."""

    def test_format_lrc(self):
        entries = [(0.0, "start"), (65.5, "one minute")]
        text = _format_lrc(entries)
        self.assertIn("[00:00.000] start", text)
        self.assertIn("[01:05.500] one minute", text)


class TestMergeSessions(unittest.TestCase):
    """Test session merging."""

    def test_merge_deduplicates(self):
        from otterrip_core.merge_sessions import run_merge_sessions

        with tempfile.TemporaryDirectory() as tmp:
            f1 = Path(tmp) / "s1"
            f2 = Path(tmp) / "s2"
            out = Path(tmp) / "out"
            f1.write_text("id1\nid2\n")
            f2.write_text("id2\nid3\n")

            args = MagicMock()
            args.output = str(out)
            args.inputs = [str(f1), str(f2)]
            run_merge_sessions(args)

            lines = out.read_text().strip().split("\n")
            self.assertEqual(len(lines), 3)
            self.assertIn("id1", lines)
            self.assertIn("id2", lines)
            self.assertIn("id3", lines)


class TestClassifyLog(unittest.TestCase):
    """Test log classification."""

    def test_classify_permanent(self):
        from otterrip_core.classify_log import classify_line
        self.assertEqual(classify_line("ERROR: 404 Not Found"), "permanent")

    def test_classify_service_unavailable(self):
        from otterrip_core.classify_log import classify_line
        self.assertEqual(classify_line("ERROR: 503 Service Unavailable"), "service_unavailable")

    def test_classify_recoverable(self):
        from otterrip_core.classify_log import classify_line
        self.assertEqual(classify_line("ERROR: Read timed out"), "recoverable")


if __name__ == "__main__":
    unittest.main()
