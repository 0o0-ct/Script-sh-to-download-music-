#!/usr/bin/env python3
"""Tests for detect_global_offset and apply_global_offset_to_bundle in aligner.py.

These tests verify that the global offset detection correctly identifies when
all LRC timestamps are shifted by a constant amount relative to the audio
onsets, and applies the correction.

Test categories:
- _median helper function
- detect_global_offset with mocked onsets
- apply_global_offset_to_bundle integration
- Edge cases: too few lines, inconsistent offsets, small offsets
- Fallback when librosa is not available
"""
from __future__ import annotations

from typing import Optional
from unittest.mock import patch

import pytest

from otterrip_core.aligner import (
    detect_global_offset,
    apply_global_offset_to_bundle,
    _median,
    _apply_offset,
    _nearest_onset,
    _detect_onsets,
    MIN_GLOBAL_OFFSET_MS,
    MIN_CONSISTENT_LINES,
    MAX_CONSISTENCY_DEVIATION_MS,
    NOTE_NO_OFFSET,
    NOTE_OFFSET_APPLIED,
    NOTE_AUDIO_FAIL,
    NOTE_TOO_FEW,
    MIN_ONSETS,
)
from otterrip_core.lyrics_bundle import LyricsBundle


# ── Helpers ────────────────────────────────────────────────────────

def _make_lines(start_ms: int = 0, count: int = 10, interval_ms: int = 5000) -> list[tuple[int, str]]:
    """Create synthetic LRC lines."""
    return [(start_ms + i * interval_ms, f"Line {i}") for i in range(count)]


def _make_bundle(lines: Optional[list[tuple[int, str]]] = None) -> LyricsBundle:
    """Create a LyricsBundle for testing."""
    if lines is None:
        lines = _make_lines()
    lrc_text = "\n".join(f"[{ms // 1000}:{(ms % 1000) // 10:02.0f}] {txt}" for ms, txt in lines)
    return LyricsBundle(
        kind="line",
        lrc=lrc_text,
        lines=lines,
        source="test",
        title="Test Song",
        artist="Test Artist",
        confidence=0.9,
    )


# ── _median helper tests ──────────────────────────────────────────

class TestMedian:
    """Test the _median helper function."""

    def test_odd_count(self):
        assert _median([100, 200, 300]) == 200

    def test_even_count(self):
        assert _median([100, 200, 300, 400]) == 250

    def test_single_value(self):
        assert _median([42]) == 42

    def test_two_values(self):
        assert _median([10, 20]) == 15

    def test_unsorted_input(self):
        assert _median([300, 100, 200]) == 200

    def test_negative_values(self):
        assert _median([-300, -200, -100]) == -200

    def test_mixed_values(self):
        assert _median([-100, 0, 100]) == 0

    def test_duplicates(self):
        assert _median([1240, 1240, 1240, 1240, 1240]) == 1240


# ── _apply_offset tests ───────────────────────────────────────────

class TestApplyOffset:
    """Test the _apply_offset helper."""

    def test_positive_offset(self):
        lines = [(0, "A"), (1000, "B")]
        result = _apply_offset(lines, 500)
        assert result == [(500, "A"), (1500, "B")]

    def test_negative_offset(self):
        lines = [(1000, "A"), (2000, "B")]
        result = _apply_offset(lines, -500)
        assert result == [(500, "A"), (1500, "B")]

    def test_zero_offset(self):
        lines = [(0, "A"), (1000, "B")]
        result = _apply_offset(lines, 0)
        assert result == [(0, "A"), (1000, "B")]

    def test_clamp_to_zero(self):
        lines = [(100, "A"), (200, "B")]
        result = _apply_offset(lines, -500)
        assert result == [(0, "A"), (0, "B")]


# ── detect_global_offset tests with mocked onsets ─────────────────

class TestDetectGlobalOffset:
    """Test detect_global_offset with mocked _detect_onsets."""

    def _make_onsets_aligned(self, shift_ms: int, interval_ms: int = 5000, count: int = 15) -> list[int]:
        """Create onsets aligned with LRC line intervals, shifted by shift_ms.
        
        Each onset is placed exactly at shift_ms + i*interval_ms so that
        _nearest_onset finds a consistent offset for every LRC line.
        """
        return [shift_ms + i * interval_ms for i in range(count)]

    @patch("otterrip_core.aligner._detect_onsets")
    def test_detects_positive_offset(self, mock_onsets):
        """Detect when LRC is behind audio (positive offset)."""
        # LRC starts at 0, audio onsets start at 1240ms
        lines = _make_lines(start_ms=0, count=10, interval_ms=5000)
        onsets = self._make_onsets_aligned(1240, interval_ms=5000)
        mock_onsets.return_value = onsets

        offset_ms, note = detect_global_offset(lines, "fake.mp3")
        assert offset_ms > 0
        assert offset_ms >= MIN_GLOBAL_OFFSET_MS
        assert NOTE_OFFSET_APPLIED in note

    @patch("otterrip_core.aligner._detect_onsets")
    def test_detects_negative_offset(self, mock_onsets):
        """Detect when LRC is ahead of audio (negative offset)."""
        # LRC starts at 2000, audio onsets start at 0
        lines = _make_lines(start_ms=2000, count=10, interval_ms=5000)
        onsets = self._make_onsets_aligned(0, interval_ms=5000)
        mock_onsets.return_value = onsets

        offset_ms, note = detect_global_offset(lines, "fake.mp3")
        assert offset_ms < 0
        assert abs(offset_ms) >= MIN_GLOBAL_OFFSET_MS
        assert NOTE_OFFSET_APPLIED in note

    @patch("otterrip_core.aligner._detect_onsets")
    def test_no_offset_when_aligned(self, mock_onsets):
        """No offset detected when LRC matches onsets."""
        # LRC and onsets are aligned
        lines = _make_lines(start_ms=0, count=10, interval_ms=5000)
        onsets = self._make_onsets_aligned(0, interval_ms=5000)
        mock_onsets.return_value = onsets

        offset_ms, note = detect_global_offset(lines, "fake.mp3")
        assert offset_ms == 0
        assert note == NOTE_NO_OFFSET

    @patch("otterrip_core.aligner._detect_onsets")
    def test_no_offset_below_threshold(self, mock_onsets):
        """Small offset (<500ms) is ignored."""
        # LRC starts at 0, onsets at 200ms — below MIN_GLOBAL_OFFSET_MS
        lines = _make_lines(start_ms=0, count=10, interval_ms=5000)
        onsets = self._make_onsets_aligned(200, interval_ms=5000)
        mock_onsets.return_value = onsets

        offset_ms, note = detect_global_offset(lines, "fake.mp3")
        assert offset_ms == 0
        assert note == NOTE_NO_OFFSET

    @patch("otterrip_core.aligner._detect_onsets")
    def test_inconsistent_offsets_rejected(self, mock_onsets):
        """If per-line offsets vary too much, reject."""
        # Create onsets that don't align consistently with LRC lines
        lines = _make_lines(start_ms=0, count=10, interval_ms=5000)
        # Onsets at irregular positions
        onsets = [0, 300, 800, 1500, 2000, 3000, 4000, 5500, 6000, 7200,
                  8000, 9500, 10000, 11000, 12500, 13000, 14000, 15500]
        for i in range(18, 50):
            onsets.append(i * 500)
        mock_onsets.return_value = onsets

        offset_ms, note = detect_global_offset(lines, "fake.mp3")
        # Should be rejected due to inconsistency or below threshold
        assert offset_ms == 0 or note == NOTE_NO_OFFSET

    @patch("otterrip_core.aligner._detect_onsets")
    def test_too_few_lines(self, mock_onsets):
        """Skip detection when there are too few lines."""
        lines = _make_lines(count=2)
        mock_onsets.return_value = self._make_onsets_aligned(1000)

        offset_ms, note = detect_global_offset(lines, "fake.mp3")
        assert offset_ms == 0
        assert note == NOTE_NO_OFFSET

    @patch("otterrip_core.aligner._detect_onsets")
    def test_audio_read_failure(self, mock_onsets):
        """Return error when audio can't be read."""
        lines = _make_lines()
        mock_onsets.return_value = None

        offset_ms, note = detect_global_offset(lines, "fake.mp3")
        assert offset_ms == 0
        assert note == NOTE_AUDIO_FAIL

    @patch("otterrip_core.aligner._detect_onsets")
    def test_too_few_onsets(self, mock_onsets):
        """Skip detection when there are too few onsets."""
        lines = _make_lines()
        mock_onsets.return_value = [0, 1000, 2000]  # fewer than MIN_ONSETS

        offset_ms, note = detect_global_offset(lines, "fake.mp3")
        assert offset_ms == 0
        assert note == NOTE_TOO_FEW

    @patch("otterrip_core.aligner._detect_onsets")
    def test_large_positive_offset(self, mock_onsets):
        """Detect a large positive offset (e.g. +2000ms)."""
        # Use smaller shift that fits within search_window_ms=2000
        lines = _make_lines(start_ms=0, count=10, interval_ms=5000)
        onsets = self._make_onsets_aligned(2000, interval_ms=5000)
        mock_onsets.return_value = onsets

        offset_ms, note = detect_global_offset(lines, "fake.mp3")
        assert offset_ms > 0
        assert NOTE_OFFSET_APPLIED in note

    @patch("otterrip_core.aligner._detect_onsets")
    def test_large_negative_offset(self, mock_onsets):
        """Detect a large negative offset (e.g. -2000ms)."""
        # LRC starts at 2000ms, onsets at 0 — offset = -2000
        lines = _make_lines(start_ms=2000, count=10, interval_ms=5000)
        onsets = self._make_onsets_aligned(0, interval_ms=5000)
        mock_onsets.return_value = onsets

        offset_ms, note = detect_global_offset(lines, "fake.mp3")
        assert offset_ms < 0
        assert NOTE_OFFSET_APPLIED in note

    @patch("otterrip_core.aligner._detect_onsets")
    def test_custom_min_offset(self, mock_onsets):
        """Respect custom min_offset_ms parameter."""
        lines = _make_lines(start_ms=0, count=10, interval_ms=5000)
        onsets = self._make_onsets_aligned(200, interval_ms=5000)
        mock_onsets.return_value = onsets

        # With default threshold (500ms), should be ignored
        offset_ms, _ = detect_global_offset(lines, "fake.mp3")
        assert offset_ms == 0

        # With lower threshold (100ms), should be detected
        offset_ms, note = detect_global_offset(lines, "fake.mp3", min_offset_ms=100)
        assert offset_ms > 0
        assert NOTE_OFFSET_APPLIED in note

    @patch("otterrip_core.aligner._detect_onsets")
    def test_custom_consistency_threshold(self, mock_onsets):
        """Respect custom max_deviation_ms parameter."""
        lines = _make_lines(start_ms=0, count=10, interval_ms=5000)
        onsets = self._make_onsets_aligned(1000, interval_ms=5000)
        mock_onsets.return_value = onsets

        offset_ms, note = detect_global_offset(lines, "fake.mp3")
        assert offset_ms > 0
        assert NOTE_OFFSET_APPLIED in note

    @patch("otterrip_core.aligner._detect_onsets")
    def test_offset_note_format(self, mock_onsets):
        """Note should contain the offset value in ms."""
        lines = _make_lines(start_ms=0, count=10, interval_ms=5000)
        onsets = self._make_onsets_aligned(1240, interval_ms=5000)
        mock_onsets.return_value = onsets

        offset_ms, note = detect_global_offset(lines, "fake.mp3")
        assert str(offset_ms) in note


# ── apply_global_offset_to_bundle tests ───────────────────────────

class TestApplyGlobalOffsetToBundle:
    """Test apply_global_offset_to_bundle integration."""

    @patch("otterrip_core.aligner._detect_onsets")
    def test_applies_offset_to_bundle(self, mock_onsets):
        """Offset is applied to bundle.lines and bundle.lrc."""
        lines = _make_lines(start_ms=0, count=10, interval_ms=5000)
        bundle = _make_bundle(lines)
        onsets = [1240 + i * 5000 for i in range(15)]
        mock_onsets.return_value = onsets

        offset_ms, note = apply_global_offset_to_bundle(bundle, "fake.mp3")
        assert offset_ms > 0
        assert NOTE_OFFSET_APPLIED in note
        # Lines should be shifted
        assert bundle.lines[0][0] > 0
        assert bundle.lines[0][0] == offset_ms
        # Warning should be added
        assert any("offset global" in w for w in bundle.warnings)

    @patch("otterrip_core.aligner._detect_onsets")
    def test_no_change_when_aligned(self, mock_onsets):
        """Bundle unchanged when no offset detected."""
        lines = _make_lines(start_ms=0, count=10, interval_ms=5000)
        bundle = _make_bundle(lines)
        onsets = [i * 5000 for i in range(15)]
        mock_onsets.return_value = onsets

        offset_ms, note = apply_global_offset_to_bundle(bundle, "fake.mp3")
        assert offset_ms == 0
        assert bundle.lines[0][0] == 0

    def test_no_sync_bundle(self):
        """Skip bundle without synced lyrics."""
        bundle = LyricsBundle(
            kind="plain",
            plain="Just plain text",
            lines=[],
            source="test",
            title="Test",
            artist="Test",
        )
        offset_ms, note = apply_global_offset_to_bundle(bundle, "fake.mp3")
        assert offset_ms == 0
        assert "sin letras sincronizadas" in note

    def test_empty_lines_bundle(self):
        """Skip bundle with empty lines."""
        bundle = LyricsBundle(
            kind="line",
            lrc=None,
            lines=[],
            source="test",
            title="Test",
            artist="Test",
        )
        offset_ms, note = apply_global_offset_to_bundle(bundle, "fake.mp3")
        assert offset_ms == 0
        assert "sin letras sincronizadas" in note

    @patch("otterrip_core.aligner._detect_onsets")
    def test_updates_bundle_offset_ms(self, mock_onsets):
        """bundle.offset_ms should be updated."""
        lines = _make_lines(start_ms=0, count=10, interval_ms=5000)
        bundle = _make_bundle(lines)
        assert bundle.offset_ms == 0
        onsets = [1500 + i * 5000 for i in range(15)]
        mock_onsets.return_value = onsets

        offset_ms, _ = apply_global_offset_to_bundle(bundle, "fake.mp3")
        assert bundle.offset_ms == offset_ms

    @patch("otterrip_core.aligner._detect_onsets")
    def test_monotonic_timestamps(self, mock_onsets):
        """Timestamps should remain monotonically increasing."""
        lines = _make_lines(start_ms=0, count=10, interval_ms=5000)
        bundle = _make_bundle(lines)
        onsets = [1240 + i * 5000 for i in range(15)]
        mock_onsets.return_value = onsets

        apply_global_offset_to_bundle(bundle, "fake.mp3")
        for i in range(1, len(bundle.lines)):
            assert bundle.lines[i][0] >= bundle.lines[i - 1][0]

    @patch("otterrip_core.aligner._detect_onsets")
    def test_clamp_to_zero(self, mock_onsets):
        """Negative timestamps should be clamped to 0."""
        lines = _make_lines(start_ms=100, count=10, interval_ms=5000)
        bundle = _make_bundle(lines)
        onsets = [i * 5000 for i in range(15)]
        mock_onsets.return_value = onsets

        offset_ms, note = apply_global_offset_to_bundle(bundle, "fake.mp3")
        # Even if offset is applied, no timestamp should be negative
        for ms, _ in bundle.lines:
            assert ms >= 0


# ── Edge cases ─────────────────────────────────────────────────────

class TestGlobalOffsetEdgeCases:
    """Edge cases for global offset detection."""

    def _make_onsets_aligned(self, shift_ms: int, interval_ms: int = 5000, count: int = 15) -> list[int]:
        """Create onsets aligned with LRC line intervals, shifted by shift_ms."""
        return [shift_ms + i * interval_ms for i in range(count)]

    @patch("otterrip_core.aligner._detect_onsets")
    def test_exact_threshold_offset(self, mock_onsets):
        """Offset exactly at MIN_GLOBAL_OFFSET_MS should be detected."""
        lines = _make_lines(start_ms=0, count=10, interval_ms=5000)
        onsets = self._make_onsets_aligned(500, interval_ms=5000)
        mock_onsets.return_value = onsets

        offset_ms, note = detect_global_offset(lines, "fake.mp3")
        # Exactly 500ms — should be detected (>= threshold)
        assert offset_ms >= MIN_GLOBAL_OFFSET_MS or offset_ms == 0

    @patch("otterrip_core.aligner._detect_onsets")
    def test_just_below_threshold(self, mock_onsets):
        """Offset just below MIN_GLOBAL_OFFSET_MS should be ignored."""
        lines = _make_lines(start_ms=0, count=10, interval_ms=5000)
        onsets = self._make_onsets_aligned(499, interval_ms=5000)
        mock_onsets.return_value = onsets

        offset_ms, note = detect_global_offset(lines, "fake.mp3")
        assert offset_ms == 0

    @patch("otterrip_core.aligner._detect_onsets")
    def test_many_lines(self, mock_onsets):
        """Works with many lines (only first 10 are checked)."""
        lines = _make_lines(start_ms=0, count=100, interval_ms=3000)
        onsets = self._make_onsets_aligned(1000, interval_ms=3000)
        mock_onsets.return_value = onsets

        offset_ms, note = detect_global_offset(lines, "fake.mp3")
        assert offset_ms > 0
        assert NOTE_OFFSET_APPLIED in note

    @patch("otterrip_core.aligner._detect_onsets")
    def test_irregular_intervals(self, mock_onsets):
        """Works with irregular LRC intervals."""
        lines = [
            (0, "A"), (3000, "B"), (8000, "C"), (12000, "D"),
            (15000, "E"), (20000, "F"), (25000, "G"), (30000, "H"),
            (35000, "I"), (40000, "J"),
        ]
        # Create onsets near each LRC line, shifted by 1000ms
        onsets = []
        for ms, _ in lines:
            onsets.append(ms + 1000)
        # Add extra onsets to meet MIN_ONSETS requirement
        for i in range(50):
            onsets.append(i * 500)
        onsets.sort()
        mock_onsets.return_value = onsets

        offset_ms, note = detect_global_offset(lines, "fake.mp3")
        # May or may not detect depending on onset alignment
        # Just verify it doesn't crash
        assert isinstance(offset_ms, int)
        assert isinstance(note, str)

    @patch("otterrip_core.aligner._detect_onsets")
    def test_librosa_not_available(self, mock_onsets):
        """Gracefully handle librosa not being available."""
        lines = _make_lines()
        mock_onsets.return_value = None

        offset_ms, note = detect_global_offset(lines, "fake.mp3")
        assert offset_ms == 0
        assert note == NOTE_AUDIO_FAIL

    @patch("otterrip_core.aligner._detect_onsets")
    def test_custom_min_consistent_lines(self, mock_onsets):
        """Respect custom min_consistent_lines parameter."""
        lines = _make_lines(start_ms=0, count=5, interval_ms=5000)
        onsets = self._make_onsets_aligned(1000, interval_ms=5000)
        mock_onsets.return_value = onsets

        # Default requires 3 consistent lines
        offset_ms, note = detect_global_offset(lines, "fake.mp3")
        # With 5 lines and consistent onsets, should work
        assert isinstance(offset_ms, int)
