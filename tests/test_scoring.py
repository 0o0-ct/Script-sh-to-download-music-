#!/usr/bin/env python3
"""Tests for _score_bundle and _select_best_bundle with fuzzy matching.

Tests the scoring function that uses rapidfuzz (or falls back to exact
matching) to rank LyricsBundle results against canonical metadata.
"""
from __future__ import annotations

from typing import Optional

import pytest

from otterrip_core.lyrics_bundle import LyricsBundle
from otterrip_core.musicbrainz import CanonicalMetadata


def _make_bundle(
    title: str,
    artist: str,
    kind: str = "line",
    ref_duration_ms: Optional[int] = None,
    confidence: float = 0.8,
    lines_count: int = 10,
    source: str = "lrclib",
) -> LyricsBundle:
    """Create a LyricsBundle for testing."""
    lines = [(i * 5000, f"Line {i}") for i in range(lines_count)]
    return LyricsBundle(
        kind=kind,
        lrc="[00:00.00] test" if kind in ("line", "word") else None,
        plain=f"Test lyrics for {title}" if kind == "plain" else None,
        lines=lines if kind in ("line", "word") else [],
        source=source,
        title=title,
        artist=artist,
        ref_duration_ms=ref_duration_ms,
        confidence=confidence,
    )


def _make_canonical(
    title: str = "Blinding Lights",
    artist: str = "The Weeknd",
    duration: Optional[float] = 200.04,
    album: str = "After Hours",
    isrc: str = "USUG11901213",
) -> CanonicalMetadata:
    """Create canonical metadata for testing."""
    return CanonicalMetadata(
        title=title,
        artist=artist,
        album=album,
        duration=duration,
        isrc=isrc,
        recording_id="test-id",
    )


# Import the scoring function
from otterrip_core.lyrics import _score_bundle, _select_best_bundle, HAS_RAPIDFUZZ


class TestScoreBundle:
    """Test _score_bundle function."""

    def test_exact_match(self):
        """Exact title and artist match should score very high."""
        bundle = _make_bundle(
            title="Blinding Lights",
            artist="The Weeknd",
            ref_duration_ms=200040,
        )
        canonical = _make_canonical()

        score = _score_bundle(bundle, canonical.title, canonical.artist, canonical.duration)
        assert score > 0.8, f"Exact match should score > 0.8, got {score}"

    def test_exact_match_word_level(self):
        """Word-level sync should score higher than line-level."""
        bundle_line = _make_bundle(
            title="Blinding Lights",
            artist="The Weeknd",
            kind="line",
            ref_duration_ms=200040,
        )
        bundle_word = _make_bundle(
            title="Blinding Lights",
            artist="The Weeknd",
            kind="word",
            ref_duration_ms=200040,
        )
        canonical = _make_canonical()

        score_line = _score_bundle(bundle_line, canonical.title, canonical.artist, canonical.duration)
        score_word = _score_bundle(bundle_word, canonical.title, canonical.artist, canonical.duration)
        assert score_word > score_line, "Word-level should score higher than line-level"

    def test_cover_version_low_score(self):
        """Cover version should score lower than original."""
        original = _make_bundle(
            title="Blinding Lights",
            artist="The Weeknd",
            ref_duration_ms=200040,
        )
        cover = _make_bundle(
            title="Blinding Lights",
            artist="Cover Artist",
            ref_duration_ms=210000,
        )
        canonical = _make_canonical()

        score_original = _score_bundle(original, canonical.title, canonical.artist, canonical.duration)
        score_cover = _score_bundle(cover, canonical.title, canonical.artist, canonical.duration)
        assert score_original > score_cover, f"Original should score higher than cover: {score_original} vs {score_cover}"

    def test_live_version_lower_score(self):
        """Live version should score lower than studio version."""
        studio = _make_bundle(
            title="Blinding Lights",
            artist="The Weeknd",
            ref_duration_ms=200040,
        )
        live = _make_bundle(
            title="Blinding Lights (Live)",
            artist="The Weeknd",
            ref_duration_ms=230000,
        )
        canonical = _make_canonical()

        score_studio = _score_bundle(studio, canonical.title, canonical.artist, canonical.duration)
        score_live = _score_bundle(live, canonical.title, canonical.artist, canonical.duration)
        assert score_studio > score_live, f"Studio should score higher than live: {score_studio} vs {score_live}"

    def test_duration_mismatch_reduces_score(self):
        """Large duration mismatch should reduce score."""
        good_duration = _make_bundle(
            title="Blinding Lights",
            artist="The Weeknd",
            ref_duration_ms=200040,
        )
        bad_duration = _make_bundle(
            title="Blinding Lights",
            artist="The Weeknd",
            ref_duration_ms=100000,  # 100s vs 200s canonical
        )
        canonical = _make_canonical()

        score_good = _score_bundle(good_duration, canonical.title, canonical.artist, canonical.duration)
        score_bad = _score_bundle(bad_duration, canonical.title, canonical.artist, canonical.duration)
        assert score_good > score_bad, f"Good duration should score higher: {score_good} vs {score_bad}"

    def test_youtube_noise_title(self):
        """Title with YouTube noise should still match with fuzzy scoring."""
        noisy = _make_bundle(
            title="Blinding Lights (Official Video)",
            artist="The Weeknd - Topic",
            ref_duration_ms=200040,
        )
        canonical = _make_canonical()

        score = _score_bundle(noisy, canonical.title, canonical.artist, canonical.duration)
        # Should still get a reasonable score due to fuzzy matching
        assert score > 0.3, f"Noisy title should still score > 0.3, got {score}"

    def test_plain_lyrics_lower_sync_score(self):
        """Plain lyrics should have lower sync component."""
        synced = _make_bundle(
            title="Blinding Lights",
            artist="The Weeknd",
            kind="line",
            ref_duration_ms=200040,
        )
        plain = _make_bundle(
            title="Blinding Lights",
            artist="The Weeknd",
            kind="plain",
            ref_duration_ms=200040,
        )
        canonical = _make_canonical()

        score_synced = _score_bundle(synced, canonical.title, canonical.artist, canonical.duration)
        score_plain = _score_bundle(plain, canonical.title, canonical.artist, canonical.duration)
        assert score_synced > score_plain, "Synced should score higher than plain"

    def test_no_canonical_metadata(self):
        """Without canonical metadata, should return bundle confidence."""
        bundle = _make_bundle(
            title="Any Song",
            artist="Any Artist",
            confidence=0.75,
        )
        score = _score_bundle(bundle, "", "", None)
        assert score == 0.75

    def test_completely_different_song(self):
        """Completely different song should score very low."""
        bundle = _make_bundle(
            title="Bohemian Rhapsody",
            artist="Queen",
            ref_duration_ms=354000,
        )
        canonical = _make_canonical()

        score = _score_bundle(bundle, canonical.title, canonical.artist, canonical.duration)
        assert score < 0.5, f"Different song should score < 0.5, got {score}"


class TestSelectBestBundle:
    """Test _select_best_bundle with canonical metadata."""

    def test_selects_highest_scored(self):
        """Should select the bundle with the highest fuzzy score."""
        bundles = [
            _make_bundle("Blinding Lights", "Cover Artist", ref_duration_ms=210000),
            _make_bundle("Blinding Lights", "The Weeknd", ref_duration_ms=200040),
            _make_bundle("Blinding Lights (Live)", "The Weeknd", ref_duration_ms=230000),
        ]
        canonical = _make_canonical()

        best = _select_best_bundle(
            bundles,
            prefer_synced=True,
            canonical=canonical,
        )
        assert best is not None
        assert best.artist == "The Weeknd"
        # Should pick the exact match, not the cover or live version

    def test_prefers_synced_with_canonical(self):
        """Synced lyrics should be preferred even with canonical scoring."""
        bundles = [
            _make_bundle("Blinding Lights", "The Weeknd", kind="plain", ref_duration_ms=200040),
            _make_bundle("Blinding Lights", "The Weeknd", kind="line", ref_duration_ms=200040),
        ]
        canonical = _make_canonical()

        best = _select_best_bundle(
            bundles,
            prefer_synced=True,
            canonical=canonical,
        )
        assert best is not None
        assert best.kind == "line"

    def test_fallback_without_canonical(self):
        """Without canonical metadata, should use original sorting logic."""
        bundles = [
            _make_bundle("Song A", "Artist A", ref_duration_ms=200000, confidence=0.5),
            _make_bundle("Song B", "Artist B", ref_duration_ms=100000, confidence=0.9),
        ]
        best = _select_best_bundle(
            bundles,
            prefer_synced=True,
            canonical=None,
        )
        assert best is not None

    def test_empty_bundles(self):
        """Empty bundle list should return None."""
        result = _select_best_bundle([], canonical=_make_canonical())
        assert result is None

    def test_all_below_confidence_threshold(self):
        """All bundles below confidence threshold should return None."""
        bundles = [
            _make_bundle("Song", "Artist", confidence=0.1),
            _make_bundle("Song2", "Artist2", confidence=0.2),
        ]
        result = _select_best_bundle(bundles, canonical=_make_canonical())
        assert result is None

    def test_official_video_vs_original(self):
        """Official Video version should score lower than clean title."""
        bundles = [
            _make_bundle("Blinding Lights (Official Video)", "The Weeknd", ref_duration_ms=200040),
            _make_bundle("Blinding Lights", "The Weeknd", ref_duration_ms=200040),
        ]
        canonical = _make_canonical()

        best = _select_best_bundle(
            bundles,
            prefer_synced=True,
            canonical=canonical,
        )
        assert best is not None
        # Clean title should win over "Official Video" variant
        assert "(Official Video)" not in best.title

    def test_remix_scores_lower(self):
        """Remix version should score lower than original."""
        bundles = [
            _make_bundle("Blinding Lights", "The Weeknd", ref_duration_ms=200040),
            _make_bundle("Blinding Lights (Kanye West Remix)", "The Weeknd", ref_duration_ms=240000),
        ]
        canonical = _make_canonical()

        best = _select_best_bundle(
            bundles,
            prefer_synced=True,
            canonical=canonical,
        )
        assert best is not None
        assert "Remix" not in best.title


class TestScoringWithRapidfuzz:
    """Tests that verify rapidfuzz behavior when available."""

    def test_rapidfuzz_available(self):
        """Check if rapidfuzz is installed."""
        if HAS_RAPIDFUZZ:
            # With rapidfuzz, similar strings should get high scores
            from rapidfuzz import fuzz
            score = fuzz.ratio("blindinglights", "blindinglights")
            assert score == 100

    def test_fuzzy_match_partial(self):
        """Partial match should get intermediate score."""
        bundle = _make_bundle(
            title="Blinding",
            artist="The Weeknd",
            ref_duration_ms=200040,
        )
        canonical = _make_canonical()

        score = _score_bundle(bundle, canonical.title, canonical.artist, canonical.duration)
        # Partial title match should be between 0 and 1
        assert 0.0 < score < 1.0
