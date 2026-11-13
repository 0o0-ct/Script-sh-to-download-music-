#!/usr/bin/env python3
"""Tests for Enhanced LRC, lyrics verify/repair/shift, and lrc_validator edge cases."""
import unittest
import tempfile
import os
from pathlib import Path
from types import SimpleNamespace

from otterrip_core.enhanced_lrc import (
    WordSpan,
    EnhancedLine,
    EnhancedLrc,
    parse_enhanced_lrc,
    serialize_enhanced_lrc,
    standard_lrc_to_enhanced,
    enhanced_to_standard_lrc,
    validate_enhanced,
)
from otterrip_core.lrc_validator import (
    parse_lrc,
    validate_and_normalise_lrc,
    normalise_lrc,
    shift_lrc,
)


# ── Enhanced LRC parser tests (≥10 edge cases) ─────────────────────

class TestEnhancedLrcParser(unittest.TestCase):
    """Test Enhanced LRC parsing and serialization."""

    def test_parse_standard_lrc(self):
        """Standard LRC lines without word timestamps."""
        text = "[00:12.34] Hello world\n[01:02.67] Second line"
        elrc = parse_enhanced_lrc(text)
        self.assertEqual(len(elrc.lines), 2)
        self.assertEqual(elrc.lines[0].line_ms, 12340)
        self.assertEqual(elrc.lines[0].raw_text, "Hello world")
        self.assertEqual(len(elrc.lines[0].words), 0)
        self.assertEqual(elrc.lines[1].line_ms, 62670)

    def test_parse_enhanced_with_words(self):
        """Enhanced LRC with word-level timestamps."""
        text = "[00:12.000] <00:12.000> Hello <00:12.400> world"
        elrc = parse_enhanced_lrc(text)
        self.assertEqual(len(elrc.lines), 1)
        self.assertEqual(len(elrc.lines[0].words), 2)
        self.assertEqual(elrc.lines[0].words[0].text, "Hello")
        self.assertEqual(elrc.lines[0].words[0].start_ms, 12000)
        self.assertEqual(elrc.lines[0].words[1].text, "world")
        self.assertEqual(elrc.lines[0].words[1].start_ms, 12400)

    def test_parse_headers(self):
        """Header tags are preserved."""
        text = "[ti:Test Song]\n[ar:Test Artist]\n[00:00.00] Line"
        elrc = parse_enhanced_lrc(text)
        self.assertEqual(elrc.headers["ti"], "Test Song")
        self.assertEqual(elrc.headers["ar"], "Test Artist")
        self.assertEqual(len(elrc.lines), 1)

    def test_parse_empty_text(self):
        """Empty input produces empty result."""
        elrc = parse_enhanced_lrc("")
        self.assertEqual(len(elrc.lines), 0)
        self.assertEqual(elrc.plain_text, "")

    def test_parse_mixed_lines(self):
        """Mix of standard and enhanced lines."""
        text = (
            "[00:05.00] Standard line\n"
            "[00:10.000] <00:10.000> Word1 <00:10.500> Word2\n"
            "[00:15.00] Another standard"
        )
        elrc = parse_enhanced_lrc(text)
        self.assertEqual(len(elrc.lines), 3)
        self.assertEqual(len(elrc.lines[0].words), 0)  # standard
        self.assertEqual(len(elrc.lines[1].words), 2)  # enhanced
        self.assertEqual(len(elrc.lines[2].words), 0)  # standard

    def test_parse_single_digit_ms(self):
        """Timestamps with 1-digit milliseconds: [00:05.1]."""
        text = "[00:05.1] Short ms"
        elrc = parse_enhanced_lrc(text)
        self.assertEqual(elrc.lines[0].line_ms, 5100)

    def test_parse_two_digit_ms(self):
        """Timestamps with 2-digit milliseconds: [00:05.12]."""
        text = "[00:05.12] Two digit ms"
        elrc = parse_enhanced_lrc(text)
        self.assertEqual(elrc.lines[0].line_ms, 5120)

    def test_parse_three_digit_ms(self):
        """Timestamps with 3-digit milliseconds: [00:05.123]."""
        text = "[00:05.123] Three digit ms"
        elrc = parse_enhanced_lrc(text)
        self.assertEqual(elrc.lines[0].line_ms, 5123)

    def test_parse_no_timestamp(self):
        """Lines without timestamps are ignored."""
        text = "No timestamp here\n[00:05.00] Has timestamp"
        elrc = parse_enhanced_lrc(text)
        self.assertEqual(len(elrc.lines), 1)

    def test_parse_blank_lines(self):
        """Blank lines are skipped."""
        text = "\n[00:05.00] Line1\n\n[00:10.00] Line2\n"
        elrc = parse_enhanced_lrc(text)
        self.assertEqual(len(elrc.lines), 2)

    def test_parse_word_end_ms_computed(self):
        """end_ms of each word = start_ms of next word."""
        text = "[00:00.000] <00:00.000> A <00:01.000> B <00:02.000> C"
        elrc = parse_enhanced_lrc(text)
        words = elrc.lines[0].words
        self.assertEqual(words[0].end_ms, 1000)
        self.assertEqual(words[1].end_ms, 2000)
        self.assertEqual(words[2].end_ms, 0)  # last word unknown

    def test_plain_text_property(self):
        """plain_text aggregates all line texts."""
        text = "[00:05.00] Line one\n[00:10.00] Line two"
        elrc = parse_enhanced_lrc(text)
        self.assertEqual(elrc.plain_text, "Line one\nLine two")

    def test_serialize_standard(self):
        """Serialize standard LRC lines."""
        elrc = EnhancedLrc(
            lines=[
                EnhancedLine(line_ms=5000, raw_text="Hello"),
                EnhancedLine(line_ms=10000, raw_text="World"),
            ]
        )
        out = serialize_enhanced_lrc(elrc)
        self.assertIn("[00:05.000] Hello", out)
        self.assertIn("[00:10.000] World", out)

    def test_serialize_enhanced(self):
        """Serialize enhanced LRC with word timestamps."""
        elrc = EnhancedLrc(
            lines=[
                EnhancedLine(
                    line_ms=12000,
                    words=[
                        WordSpan(text="Hello", start_ms=12000, end_ms=12400),
                        WordSpan(text="world", start_ms=12400, end_ms=0),
                    ],
                    raw_text="Hello world",
                )
            ]
        )
        out = serialize_enhanced_lrc(elrc)
        self.assertIn("<00:12.000> Hello", out)
        self.assertIn("<00:12.400> world", out)

    def test_serialize_headers(self):
        """Headers appear in serialized output."""
        elrc = EnhancedLrc(
            headers={"ti": "Song", "ar": "Artist"},
            lines=[EnhancedLine(line_ms=0, raw_text="Text")],
        )
        out = serialize_enhanced_lrc(elrc)
        self.assertIn("[ti:Song]", out)
        self.assertIn("[ar:Artist]", out)


class TestStandardToEnhanced(unittest.TestCase):
    """Test conversion from standard LRC to Enhanced skeleton."""

    def test_basic_conversion(self):
        text = "[00:05.00] Hello world\n[00:10.00] Goodbye"
        elrc = standard_lrc_to_enhanced(text)
        self.assertEqual(len(elrc.lines), 2)
        self.assertEqual(len(elrc.lines[0].words), 2)  # "Hello", "world"
        self.assertEqual(elrc.lines[0].words[0].text, "Hello")
        self.assertEqual(elrc.lines[0].words[0].start_ms, 5000)

    def test_empty_input(self):
        elrc = standard_lrc_to_enhanced("")
        self.assertEqual(len(elrc.lines), 0)


class TestEnhancedToStandard(unittest.TestCase):
    """Test conversion from Enhanced back to standard LRC."""

    def test_roundtrip(self):
        text = "[00:05.00] Hello world\n[00:10.00] Goodbye"
        elrc = standard_lrc_to_enhanced(text)
        back = enhanced_to_standard_lrc(elrc)
        self.assertIn("Hello world", back)
        self.assertIn("Goodbye", back)


class TestValidateEnhanced(unittest.TestCase):
    """Test Enhanced LRC validation."""

    def test_valid_enhanced(self):
        elrc = EnhancedLrc(
            lines=[
                EnhancedLine(
                    line_ms=5000,
                    words=[
                        WordSpan(text="A", start_ms=5000),
                        WordSpan(text="B", start_ms=5500),
                    ],
                )
            ]
        )
        warnings = validate_enhanced(elrc)
        self.assertEqual(len(warnings), 0)

    def test_non_monotonic_words(self):
        elrc = EnhancedLrc(
            lines=[
                EnhancedLine(
                    line_ms=5000,
                    words=[
                        WordSpan(text="A", start_ms=6000),
                        WordSpan(text="B", start_ms=5000),  # goes backward
                    ],
                )
            ]
        )
        warnings = validate_enhanced(elrc)
        self.assertTrue(any("not monotonic" in w for w in warnings))

    def test_word_far_from_line(self):
        elrc = EnhancedLrc(
            lines=[
                EnhancedLine(
                    line_ms=5000,
                    words=[WordSpan(text="A", start_ms=10000)],  # 5s away
                )
            ]
        )
        warnings = validate_enhanced(elrc)
        self.assertTrue(any("far from" in w for w in warnings))

    def test_non_monotonic_lines(self):
        elrc = EnhancedLrc(
            lines=[
                EnhancedLine(line_ms=10000, words=[WordSpan("A", 10000)]),
                EnhancedLine(line_ms=5000, words=[WordSpan("B", 5000)]),  # backward
            ]
        )
        warnings = validate_enhanced(elrc)
        self.assertTrue(any("line timestamp" in w for w in warnings))


# ── LRC validator edge cases ────────────────────────────────────────

class TestLrcValidatorEdgeCases(unittest.TestCase):
    """Additional edge cases for lrc_validator."""

    def test_shift_positive(self):
        text = "[00:05.00] Hello\n[00:10.00] World"
        shifted = shift_lrc(text, 1000)
        entries = parse_lrc(shifted)
        self.assertEqual(entries[0][0], 6000)
        self.assertEqual(entries[1][0], 11000)

    def test_shift_negative(self):
        text = "[00:05.00] Hello\n[00:10.00] World"
        shifted = shift_lrc(text, -2000)
        entries = parse_lrc(shifted)
        self.assertEqual(entries[0][0], 3000)
        self.assertEqual(entries[1][0], 8000)

    def test_shift_negative_clamp_zero(self):
        text = "[00:01.00] Hello"
        shifted = shift_lrc(text, -5000)
        entries = parse_lrc(shifted)
        self.assertEqual(entries[0][0], 0)  # clamped to 0

    def test_validate_empty(self):
        result = validate_and_normalise_lrc("")
        self.assertFalse(result.valid)

    def test_validate_plain_text_only(self):
        result = validate_and_normalise_lrc("Just plain text\nNo timestamps")
        self.assertFalse(result.valid)

    def test_normalise_lrc_format(self):
        entries = [(5000, "Hello"), (10000, "World")]
        out = normalise_lrc(entries)
        self.assertIn("[00:05.00]", out)
        self.assertIn("[00:10.00]", out)


# ── Lyrics verify/repair/shift tests ───────────────────────────────

class TestLyricsVerify(unittest.TestCase):
    """Test lyrics verify with synthetic audio files."""

    @classmethod
    def setUpClass(cls):
        """Create synthetic test audio files."""
        cls.tmpdir = tempfile.mkdtemp()
        try:
            import subprocess
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i",
                 "sine=frequency=440:duration=3",
                 "-codec:a", "libmp3lame",
                 os.path.join(cls.tmpdir, "test.mp3")],
                capture_output=True, timeout=10,
            )
            cls.has_ffmpeg = True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            cls.has_ffmpeg = False

    @unittest.skipUnless(os.environ.get("HAS_FFMPEG"), "ffmpeg not available")
    def test_verify_no_lyrics(self):
        """A file without lyrics should be FAIL."""
        from otterrip_core.lyrics_verify import verify_track
        fpath = Path(self.tmpdir) / "test.mp3"
        if not fpath.exists():
            self.skipTest("test.mp3 not created")
        v = verify_track(fpath)
        self.assertEqual(v.status, "FAIL")

    def test_verify_track_import(self):
        """verify_track function exists and is callable."""
        from otterrip_core.lyrics_verify import verify_track, TrackVerdict
        self.assertTrue(callable(verify_track))


class TestLyricsRepair(unittest.TestCase):
    """Test lyrics repair functions."""

    def test_lang_iso3_mapping(self):
        from otterrip_core.lyrics_verify import _lang_iso3
        self.assertEqual(_lang_iso3("es"), "spa")
        self.assertEqual(_lang_iso3("en"), "eng")
        self.assertEqual(_lang_iso3("pt"), "por")
        self.assertEqual(_lang_iso3("fr"), "fre")
        self.assertEqual(_lang_iso3(""), "eng")
        self.assertEqual(_lang_iso3("xx"), "eng")  # unknown

    def test_is_lrc_text(self):
        from otterrip_core.lyrics_verify import _is_lrc_text
        self.assertTrue(_is_lrc_text("[00:05.00] Hello"))
        self.assertTrue(_is_lrc_text("[0:05.00] Hello"))
        self.assertFalse(_is_lrc_text("Just plain text"))
        self.assertFalse(_is_lrc_text(""))


class TestLyricsShift(unittest.TestCase):
    """Test lyrics shift dispatcher."""

    def test_run_lyrics_manage_verify(self):
        """lyrics-manage verify dispatches correctly."""
        from otterrip_core.lyrics_verify import run_lyrics_manage
        args = SimpleNamespace(
            lyrics_manage_cmd="verify",
            path=tempfile.gettempdir(),
        )
        # Should not crash; returns 0 or 1 depending on content
        rc = run_lyrics_manage(args)
        self.assertIn(rc, (0, 1))

    def test_run_lyrics_manage_no_cmd(self):
        """No subcommand returns 1."""
        from otterrip_core.lyrics_verify import run_lyrics_manage
        args = SimpleNamespace(lyrics_manage_cmd=None)
        rc = run_lyrics_manage(args)
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
