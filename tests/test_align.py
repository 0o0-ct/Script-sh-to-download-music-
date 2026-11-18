#!/usr/bin/env python3
"""Tests for otterrip_core.align (lyrics-to-audio offset estimation).

These tests are pure-Python: they build synthetic onset envelopes and
synthetic LRC bundles so they run without librosa/numpy installed.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

import otterrip_core.align as align_module
from otterrip_core.align import (
    DEFAULT_MAX_SHIFT_MS,
    MIN_LINES,
    estimate_offset_ms,
    shift_entries,
)
from otterrip_core.lyrics_bundle import LyricsBundle


def _synthetic_envelope(ms_per_frame=20.0, duration_ms=200_000):
    """Flat envelope with a gaussian bump every 4 s (a steady beat)."""
    n = int(duration_ms / ms_per_frame)
    env = [0.05] * n
    beat_ms = 4000.0
    width_ms = 120.0
    for i in range(n):
        t = i * ms_per_frame
        # distance to nearest beat, wrapped
        phase = ((t + beat_ms / 2) % beat_ms) - beat_ms / 2
        if abs(phase) < 3 * width_ms:
            env[i] += 0.95 * pow(2.718281828459045, -(phase / width_ms) ** 2)
    return env, ms_per_frame


def _line_starts(count=20, spacing_ms=8000, first_ms=16_000):
    # spacing multiple of the beat period → every line can sit on a beat
    # with ONE constant offset (required by the single-offset model).
    return [first_ms + i * spacing_ms for i in range(count)]


@unittest.skipUnless(
    align_module.HAS_LIBROSA and shutil.which("ffmpeg"),
    "requiere librosa + ffmpeg",
)
class TestOnsetEnvelopeM4a(unittest.TestCase):
    """Regression: libsndfile cannot read AAC/M4A — the ffmpeg fallback must."""

    def test_onset_envelope_reads_m4a(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "song.m4a")
            subprocess.run(
                [
                    "ffmpeg", "-v", "error", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=5",
                    "-b:a", "32k", path, "-y",
                ],
                check=True,
            )
            result = align_module.onset_envelope(path)
        self.assertIsNotNone(result)
        env, frame_ms = result
        self.assertGreater(len(env), 50)
        self.assertGreater(frame_ms, 0)


class TestEstimateOffset(unittest.TestCase):
    def setUp(self):
        self.env, self.frame_ms = _synthetic_envelope()
        # Score landscape moves in ~frame_ms steps; allow a few frames.
        self.tol = 3 * self.frame_ms

    def test_recovers_small_known_offset(self):
        # Lines land exactly on beats + 400 ms → best shift should be -400.
        starts = [s + 400 for s in _line_starts()]
        offset, conf = estimate_offset_ms(self.env, self.frame_ms, starts)
        self.assertAlmostEqual(offset, -400, delta=self.tol)
        self.assertGreaterEqual(conf, 0.5)

    def test_recovers_negative_offset(self):
        # Lines land 600 ms BEFORE a beat → positive shift needed.
        starts = [s - 600 for s in _line_starts()]
        offset, conf = estimate_offset_ms(self.env, self.frame_ms, starts)
        self.assertAlmostEqual(offset, 600, delta=self.tol)
        self.assertGreaterEqual(conf, 0.5)

    def test_zero_offset_when_already_aligned(self):
        starts = _line_starts()
        offset, conf = estimate_offset_ms(self.env, self.frame_ms, starts)
        self.assertAlmostEqual(offset, 0, delta=self.tol)
        self.assertGreaterEqual(conf, 0.5)

    def test_few_lines_zero_confidence(self):
        offset, conf = estimate_offset_ms(
            self.env, self.frame_ms, _line_starts(MIN_LINES - 1)
        )
        self.assertEqual(offset, 0)
        self.assertEqual(conf, 0.0)

    def test_noise_envelope_low_confidence(self):
        import random

        rng = random.Random(1234)
        noise = [rng.random() for _ in range(len(self.env))]
        offset, conf = estimate_offset_ms(noise, self.frame_ms, _line_starts())
        self.assertLess(conf, 0.55, msg=f"conf={conf}, offset={offset}")

    def test_respects_max_shift(self):
        starts = [s + 20_000 for s in _line_starts()]  # beyond ±15 s window
        offset, conf = estimate_offset_ms(self.env, self.frame_ms, starts)
        self.assertLessEqual(abs(offset), DEFAULT_MAX_SHIFT_MS)

    def test_bad_inputs(self):
        self.assertEqual(estimate_offset_ms([], 20.0, _line_starts()), (0, 0.0))
        self.assertEqual(estimate_offset_ms(self.env, 0.0, _line_starts()), (0, 0.0))


class TestShiftEntries(unittest.TestCase):
    def test_positive_shift(self):
        out = shift_entries([(1000, "a"), (2000, "b")], 500, 10_000)
        self.assertEqual(out, [(1500, "a"), (2500, "b")])

    def test_negative_shift_clamps_at_zero(self):
        out = shift_entries([(100, "a"), (2000, "b")], -500, 10_000)
        self.assertEqual(out[0][0], 0)
        self.assertEqual(out[1], (1500, "b"))

    def test_rejects_if_last_ts_exceeds_duration(self):
        # last ts 9000 + shift 4500 = 13500 > 10000 + 3000 → rejected
        self.assertIsNone(
            shift_entries([(1000, "a"), (9000, "b")], 4500, 10_000)
        )

    def test_allows_shift_up_to_duration_plus_tolerance(self):
        out = shift_entries([(1000, "a"), (9000, "b")], 2000, 10_000)
        self.assertEqual(out[-1][0], 11_000)

    def test_empty_lines(self):
        self.assertIsNone(shift_entries([], 500, 10_000))

    def test_output_sorted(self):
        out = shift_entries([(2000, "b"), (1000, "a")], 0)
        self.assertEqual([t for _, t in out], ["a", "b"])


def _make_bundle(lines):
    """LyricsBundle with only the fields auto_align_bundle touches."""
    return LyricsBundle(
        kind="line", lines=list(lines), title="t", artist="a",
    )


class TestAutoAlignBundle(unittest.TestCase):
    def test_without_librosa_note(self):
        import otterrip_core.align as align

        bundle = _make_bundle(
            [(i * 9000, f"line {i}") for i in range(10)]
        )
        # Force the "not installed" path deterministically.
        had = align.HAS_LIBROSA
        try:
            align.HAS_LIBROSA = False
            applied, conf, note = align.auto_align_bundle(
                bundle, "/nonexistent/file.mp3"
            )
            self.assertEqual(applied, 0)
            self.assertEqual(conf, 0.0)
            self.assertIn("librosa", note)
            # Nothing changed.
            self.assertEqual(bundle.lines[0][0], 0)
        finally:
            align.HAS_LIBROSA = had

    def test_unreadable_audio_with_librosa_note(self):
        import otterrip_core.align as align

        bundle = _make_bundle(
            [(i * 9000, f"line {i}") for i in range(10)]
        )
        had = align.HAS_LIBROSA
        try:
            align.HAS_LIBROSA = True
            # onset_envelope catches all exceptions → None with librosa "on".
            applied, conf, note = align.auto_align_bundle(
                bundle, "/nonexistent/file.mp3"
            )
            self.assertEqual(applied, 0)
            self.assertIn("no se pudo leer el audio", note)
        finally:
            align.HAS_LIBROSA = had

    def test_no_synced_lyrics_is_noop(self):
        import otterrip_core.align as align

        bundle = _make_bundle([])
        bundle.kind = "plain"
        applied, conf, note = align.auto_align_bundle(
            bundle, "/nonexistent/file.mp3"
        )
        self.assertEqual((applied, conf, note), (0, 0.0, ""))


if __name__ == "__main__":
    unittest.main()
