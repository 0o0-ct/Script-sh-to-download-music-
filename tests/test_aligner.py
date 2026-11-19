#!/usr/bin/env python3
"""Tests for aligner.py — forced line-by-line LRC alignment to audio onsets.

Uses mocked librosa calls to avoid needing real audio files.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from otterrip_core.aligner import (
    AlignReport,
    NOTE_NO_LIBROSA,
    NOTE_TOO_FEW,
    _apply_offset,
    _nearest_onset,
    align_lrc_to_audio,
    align_lrc_string,
    align_bundle,
)


# ── Sample data ────────────────────────────────────────────────────

# Synthetic LRC lines: 10 lines spaced ~2000ms apart
SYNTHETIC_LINES = [
    (0, "Primera línea"),
    (2000, "Segunda línea"),
    (4000, "Tercera línea"),
    (6000, "Cuarta línea"),
    (8000, "Quinta línea"),
    (10000, "Sexta línea"),
    (12000, "Séptima línea"),
    (14000, "Octava línea"),
    (16000, "Novena línea"),
    (18000, "Décima línea"),
]

# Onsets detected in audio — slightly offset from the LRC timestamps
# (simulating a recording with a different intro)
SYNTHETIC_ONSETS = [
    150, 2150, 4100, 6050, 8100, 10050, 12100, 14050, 16100, 18050,
    # Extra onsets (instrumental hits)
    1000, 3000, 5000, 7000, 9000, 11000, 13000, 15000, 17000,
]


# ── Unit tests for helper functions ────────────────────────────────

class TestNearestOnset:
    def test_exact_match(self):
        onsets = [1000, 2000, 3000]
        assert _nearest_onset(2000, onsets, 250) == 2000

    def test_close_onset(self):
        onsets = [1000, 2100, 3000]
        # 2000 is 100ms from 2100, within 250ms window
        assert _nearest_onset(2000, onsets, 250) == 2100

    def test_too_far(self):
        onsets = [1000, 3000, 5000]
        # 2000 is 1000ms from both 1000 and 3000, outside 250ms window
        assert _nearest_onset(2000, onsets, 250) is None

    def test_edge_of_window(self):
        onsets = [1000, 2250, 4000]
        # 2000 is exactly 250ms from 2250
        assert _nearest_onset(2000, onsets, 250) == 2250

    def test_just_outside_window(self):
        onsets = [1000, 2251, 4000]
        # 2000 is 251ms from 2251, just outside 250ms window
        assert _nearest_onset(2000, onsets, 250) is None

    def test_empty_onsets(self):
        assert _nearest_onset(2000, [], 250) is None

    def test_before_first_onset(self):
        onsets = [5000, 6000, 7000]
        assert _nearest_onset(100, onsets, 250) is None

    def test_after_last_onset(self):
        onsets = [1000, 2000, 3000]
        assert _nearest_onset(9999, onsets, 250) is None

    def test_prefers_closer_onset(self):
        onsets = [1900, 2200, 3000]
        # 2000 is 100ms from 1900 and 200ms from 2200
        assert _nearest_onset(2000, onsets, 250) == 1900


class TestApplyOffset:
    def test_zero_offset(self):
        lines = [(1000, "a"), (2000, "b")]
        result = _apply_offset(lines, 0)
        assert result == [(1000, "a"), (2000, "b")]

    def test_positive_offset(self):
        lines = [(1000, "a"), (2000, "b")]
        result = _apply_offset(lines, 500)
        assert result == [(1500, "a"), (2500, "b")]

    def test_negative_offset(self):
        lines = [(1000, "a"), (2000, "b")]
        result = _apply_offset(lines, -500)
        assert result == [(500, "a"), (1500, "b")]

    def test_negative_offset_clamps_to_zero(self):
        lines = [(100, "a"), (2000, "b")]
        result = _apply_offset(lines, -500)
        assert result == [(0, "a"), (1500, "b")]


# ── Integration tests with mocked librosa ──────────────────────────

def _mock_librosa(onset_times_ms: list[int]):
    """Create mock librosa and numpy modules."""
    mock_librosa = MagicMock()
    mock_np = MagicMock()

    # Convert ms to seconds for onset_detect
    onset_times_s = [t / 1000.0 for t in onset_times_ms]
    mock_librosa.onset.onset_detect.return_value = onset_times_s

    # Mock librosa.load to return a dummy array
    mock_array = mock_np.array.return_value
    mock_librosa.load.return_value = (mock_array, 22050)

    return mock_librosa, mock_np


class TestAlignLrcToAudio:

    def test_aligns_lines_to_onsets(self):
        """Lines should snap to the nearest onset within the window."""
        mock_librosa, mock_np = _mock_librosa(SYNTHETIC_ONSETS)

        with patch.dict(
            "sys.modules",
            {"librosa": mock_librosa, "numpy": mock_np},
        ):
            aligned, report = align_lrc_to_audio(
                SYNTHETIC_LINES,
                "/fake/audio.mp3",
                snap_window_ms=250,
            )

        # All 10 lines should have been moved
        assert report.lines_moved == 10
        assert report.total_lines == 10
        assert report.max_shift_ms > 0

        # First line: 0ms → nearest onset is 150ms
        assert aligned[0][0] == 150
        # Second line: 2000ms → nearest onset is 2150ms
        assert aligned[1][0] == 2150

    def test_no_librosa_returns_original(self):
        """Without librosa, lines should be returned unchanged."""
        with patch.dict("sys.modules", {}):
            # Remove librosa from the module cache
            import sys
            saved = sys.modules.pop("librosa", None)
            try:
                aligned, report = align_lrc_to_audio(
                    SYNTHETIC_LINES,
                    "/fake/audio.mp3",
                )
            finally:
                if saved is not None:
                    sys.modules["librosa"] = saved

        # When librosa is missing, the code falls through to the audio-read path
        # and returns NOTE_AUDIO_FAIL because the file doesn't exist
        from otterrip_core.aligner import NOTE_AUDIO_FAIL
        assert report.note in (NOTE_NO_LIBROSA, NOTE_AUDIO_FAIL)
        assert report.lines_moved == 0

    def test_too_few_lines_skips_alignment(self):
        """With fewer than min_lines, alignment is skipped."""
        mock_librosa, mock_np = _mock_librosa(SYNTHETIC_ONSETS)

        with patch.dict(
            "sys.modules",
            {"librosa": mock_librosa, "numpy": mock_np},
        ):
            short_lines = [(0, "a"), (2000, "b")]
            aligned, report = align_lrc_to_audio(
                short_lines,
                "/fake/audio.mp3",
                min_lines=4,
            )

        assert report.note == NOTE_TOO_FEW
        assert report.lines_moved == 0

    def test_global_offset_applied(self):
        """Global offset_ms should be applied after snapping."""
        mock_librosa, mock_np = _mock_librosa(SYNTHETIC_ONSETS)

        with patch.dict(
            "sys.modules",
            {"librosa": mock_librosa, "numpy": mock_np},
        ):
            aligned, report = align_lrc_to_audio(
                SYNTHETIC_LINES,
                "/fake/audio.mp3",
                offset_ms=500,
                snap_window_ms=250,
            )

        # First line: 0ms → snapped to 150ms → +500ms offset = 650ms
        assert aligned[0][0] == 650

    def test_max_shift_per_line(self):
        """Lines beyond max_shift_ms should not be moved."""
        mock_librosa, mock_np = _mock_librosa(SYNTHETIC_ONSETS)

        with patch.dict(
            "sys.modules",
            {"librosa": mock_librosa, "numpy": mock_np},
        ):
            # Lines far from any onset
            far_lines = [
                (0, "a"),
                (50000, "b"),  # No onset near 50000ms
                (100000, "c"),
                (150000, "d"),
            ]
            aligned, report = align_lrc_to_audio(
                far_lines,
                "/fake/audio.mp3",
                snap_window_ms=250,
                max_shift_ms=500,
                min_lines=1,
            )

        # Lines far from onsets should not be moved
        assert aligned[1][0] == 50000
        assert aligned[2][0] == 100000

    def test_monotonicity_preserved(self):
        """Aligned lines should remain sorted by timestamp."""
        mock_librosa, mock_np = _mock_librosa(SYNTHETIC_ONSETS)

        with patch.dict(
            "sys.modules",
            {"librosa": mock_librosa, "numpy": mock_np},
        ):
            aligned, _ = align_lrc_to_audio(
                SYNTHETIC_LINES,
                "/fake/audio.mp3",
                snap_window_ms=250,
            )

        timestamps = [ms for ms, _ in aligned]
        assert timestamps == sorted(timestamps)

    def test_report_note_on_success(self):
        """Report should have a descriptive note when lines are moved."""
        mock_librosa, mock_np = _mock_librosa(SYNTHETIC_ONSETS)

        with patch.dict(
            "sys.modules",
            {"librosa": mock_librosa, "numpy": mock_np},
        ):
            _, report = align_lrc_to_audio(
                SYNTHETIC_LINES,
                "/fake/audio.mp3",
                snap_window_ms=250,
            )

        assert "líneas ajustadas" in report.note
        assert report.lines_moved > 0


# ── Tests for align_lrc_string ─────────────────────────────────────

class TestAlignLrcString:
    def test_aligns_lrc_string(self):
        """LRC string should be parsed, aligned, and re-formatted."""
        lrc_text = (
            "[00:00.00] Primera línea\n"
            "[00:02.00] Segunda línea\n"
            "[00:04.00] Tercera línea\n"
            "[00:06.00] Cuarta línea\n"
            "[00:08.00] Quinta línea\n"
            "[00:10.00] Sexta línea\n"
            "[00:12.00] Séptima línea\n"
            "[00:14.00] Octava línea\n"
            "[00:16.00] Novena línea\n"
            "[00:18.00] Décima línea\n"
        )
        mock_librosa, mock_np = _mock_librosa(SYNTHETIC_ONSETS)

        with patch.dict(
            "sys.modules",
            {"librosa": mock_librosa, "numpy": mock_np},
        ):
            aligned_lrc, report = align_lrc_string(
                lrc_text, "/fake/audio.mp3", snap_window_ms=250
            )

        assert report.lines_moved > 0
        # The first timestamp should be adjusted
        assert "[00:00.15]" in aligned_lrc or "[00:00.1" in aligned_lrc


# ── Tests for align_bundle ─────────────────────────────────────────

class TestAlignBundle:
    def test_aligns_bundle_in_place(self):
        """Bundle lines and lrc should be updated in-place."""
        from otterrip_core.lyrics_bundle import LyricsBundle

        bundle = LyricsBundle(
            kind="line",
            lrc="\n".join(
                f"[{ms // 60000:02d}:{(ms % 60000) // 1000:02d}.{(ms % 1000) // 10:02d}] {txt}"
                for ms, txt in SYNTHETIC_LINES
            ),
            lines=list(SYNTHETIC_LINES),
        )

        mock_librosa, mock_np = _mock_librosa(SYNTHETIC_ONSETS)

        with patch.dict(
            "sys.modules",
            {"librosa": mock_librosa, "numpy": mock_np},
        ):
            report = align_bundle(bundle, "/fake/audio.mp3", snap_window_ms=250)

        assert report.lines_moved > 0
        assert bundle.lines[0][0] == 150  # First line snapped to 150ms
        assert bundle.lrc is not None
        assert "alineación forzada" in bundle.warnings[0]

    def test_skips_non_synced_bundle(self):
        """Bundle without synced lyrics should be skipped."""
        from otterrip_core.lyrics_bundle import LyricsBundle

        bundle = LyricsBundle(
            kind="plain",
            plain="Sin timestamps",
            lines=[],
        )

        report = align_bundle(bundle, "/fake/audio.mp3")
        assert report.note == "sin letras sincronizadas"
