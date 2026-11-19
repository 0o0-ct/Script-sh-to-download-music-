#!/usr/bin/env python3
"""Tests for the word-level alignment engine (Enhanced LRC).

Pure, deterministic: ``otterrip_core.aligner._detect_onsets`` is
mocked, so no audio files are needed.
"""

import unittest
from unittest.mock import patch

from otterrip_core.aligner import (
    NOTE_AUDIO_FAIL,
    NOTE_TOO_FEW,
    WORD_NOTE_NO_WORDS,
    align_words_to_audio,
)
from otterrip_core.enhanced_lrc import (
    align_words_in_place,
    parse_enhanced_lrc,
    serialize_enhanced_lrc,
    validate_enhanced,
)

ONSETS = [
    0, 400, 900, 1400, 1900, 2400, 2900, 3400, 3900, 4400,
    4900, 5400, 5900, 6400, 6900, 7400, 7900, 8400, 8900, 9400, 9800,
]

TEST_ELRC = "[ti:Test]\n[ar:Artist]\n[00:01.00] hola mundo ahora\n"


class TestAlignWordsToAudio(unittest.TestCase):
    """aligner.align_words_to_audio: snapping and guards."""

    def _run(self, lines, onsets=ONSETS):
        with patch("otterrip_core.aligner._detect_onsets", return_value=onsets):
            return align_words_to_audio(lines, "/nonexistent/audio.wav")

    def test_snaps_word_starts_to_nearest_onset(self):
        out, report = self._run([(1000, ("hola", "mundo", "ahora"))])
        starts = [t.start_ms for _, timings in out for t in timings]
        self.assertEqual(starts, [1000, 1150, 1400])
        self.assertEqual(report.lines_done, 1)
        self.assertEqual(report.total_words, 3)

    def test_last_word_ends_after_start(self):
        out, _ = self._run([(1000, ("hola", "mundo", "ahora"))])
        timings = out[0][1]
        self.assertEqual(timings[2].end_ms, timings[2].start_ms + 240)
        self.assertEqual(timings[0].end_ms, timings[1].start_ms)

    def test_falls_back_to_line_ms_without_nearby_onsets(self):
        out, _ = self._run([(1000, ("hola", "mundo"))], onsets=[0, 9000, 9500, 9600])
        starts = [t.start_ms for _, timings in out for t in timings]
        # Nothing within the snap window: starts fall back to the cursor.
        self.assertEqual(starts, [1000, 1150])

    def test_no_onsets(self):
        out, report = self._run([(1000, ("hola",))], onsets=None)
        self.assertEqual(out, [])
        self.assertEqual(report.note, NOTE_AUDIO_FAIL)

    def test_too_few_onsets(self):
        out, report = self._run([(1000, ("hola",))], onsets=[0, 5000])
        self.assertEqual(out, [])
        self.assertEqual(report.note, NOTE_TOO_FEW)

    def test_no_words(self):
        out, report = self._run([(1000, ())])
        self.assertEqual(out, [])
        self.assertEqual(report.note, WORD_NOTE_NO_WORDS)

    def test_empty_lines(self):
        out, report = self._run([])
        self.assertEqual(out, [])
        self.assertEqual(report.note, WORD_NOTE_NO_WORDS)


class TestAlignWordsInPlace(unittest.TestCase):
    """enhanced_lrc.align_words_in_place: bundle-level integration."""

    def _run(self, text=TEST_ELRC, onsets=ONSETS):
        elrc = parse_enhanced_lrc(text)
        with patch("otterrip_core.aligner._detect_onsets", return_value=onsets):
            notes, report = align_words_in_place(elrc, "/nonexistent/audio.wav")
        return elrc, notes, report

    def test_fills_word_timings_and_validates(self):
        elrc, notes, report = self._run()
        self.assertEqual(report.lines_done, 1)
        self.assertEqual(report.total_words, 3)
        line = elrc.lines[0]
        self.assertEqual([w.text for w in line.words], ["hola", "mundo", "ahora"])
        self.assertEqual([w.start_ms for w in line.words], [1000, 1150, 1400])
        out = serialize_enhanced_lrc(elrc)
        # Centisecond format (2-digit ms part).
        self.assertIn("<00:01.00> hola", out)
        self.assertIn("<00:01.15> mundo", out)
        self.assertIn("<00:01.40> ahora", out)
        # Validation is clean: first word within tolerance of the line ts.
        self.assertEqual(validate_enhanced(elrc), [])

    def test_realigns_existing_word_timings(self):
        elrc, _, report = self._run(text="[00:01.00] <00:01.000> hola <00:01.200> mundo\n")
        self.assertEqual(report.lines_done, 1)
        self.assertEqual([w.start_ms for w in elrc.lines[0].words], [1000, 1150])

    def test_no_onsets_leaves_structure_unchanged(self):
        elrc, notes, _ = self._run(onsets=None)
        self.assertEqual(notes, [NOTE_AUDIO_FAIL])
        self.assertEqual(elrc.lines[0].words, [])


if __name__ == "__main__":
    unittest.main()
