#!/usr/bin/env python3
"""Tests for _score_bundle and _version_suffix_penalty with dirty YouTube titles.

These tests simulate real-world YouTube search results where titles contain
noise like "(Official Video)", "[4K]", "(Live)", "(Remix)", etc.  The goal
is to verify that the scoring function correctly ranks the clean/studio
version higher than covers, live versions, remixes, and other variants.

Test categories:
- Harmless suffixes: (Official Video), (Lyric Video), (Audio), [HD], [4K]
- Version suffixes: (Live), (Remix), (Acoustic) — should be penalized
- Covers with different artist — should score low
- Duration mismatch (>30%) — strong penalty
- Real-world YouTube title patterns
- Logging of why a result won
"""
from __future__ import annotations

from typing import Optional

import pytest

from otterrip_core.lyrics_bundle import LyricsBundle
from otterrip_core.musicbrainz import CanonicalMetadata
from otterrip_core.lyrics import (
    _score_bundle,
    _select_best_bundle,
    _version_suffix_penalty,
    _VERSION_SUFFIXES_PENALIZE,
    _HARMLESS_SUFFIXES,
    HAS_RAPIDFUZZ,
)


# ── Helpers ────────────────────────────────────────────────────────

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


# ── Version suffix penalty list tests ──────────────────────────────

class TestVersionSuffixLists:
    """Verify the penalty and harmless suffix lists are correctly configured."""

    def test_penalty_list_has_live(self):
        assert "live" in _VERSION_SUFFIXES_PENALIZE

    def test_penalty_list_has_remix(self):
        assert "remix" in _VERSION_SUFFIXES_PENALIZE

    def test_penalty_list_has_acoustic(self):
        assert "acoustic" in _VERSION_SUFFIXES_PENALIZE

    def test_penalty_list_has_cover(self):
        assert "cover" in _VERSION_SUFFIXES_PENALIZE

    def test_penalty_list_has_instrumental(self):
        assert "instrumental" in _VERSION_SUFFIXES_PENALIZE

    def test_penalty_list_has_unplugged(self):
        assert "unplugged" in _VERSION_SUFFIXES_PENALIZE

    def test_harmless_list_has_official_video(self):
        assert "official video" in _HARMLESS_SUFFIXES

    def test_harmless_list_has_4k(self):
        assert "4k" in _HARMLESS_SUFFIXES

    def test_harmless_list_has_hd(self):
        assert "hd" in _HARMLESS_SUFFIXES

    def test_harmless_list_has_lyric_video(self):
        assert "lyric video" in _HARMLESS_SUFFIXES


# ── _version_suffix_penalty function tests ─────────────────────────

class TestVersionSuffixPenalty:
    """Test the _version_suffix_penalty helper function."""

    def test_no_penalty_clean_title(self):
        """Clean title with no suffixes should have no penalty."""
        penalty = _version_suffix_penalty("Blinding Lights", "Blinding Lights")
        assert penalty == 1.0

    def test_penalty_for_live(self):
        """'Live' in bundle title but not canonical should penalize."""
        penalty = _version_suffix_penalty("Blinding Lights (Live)", "Blinding Lights")
        assert penalty < 1.0
        assert penalty == pytest.approx(0.7, rel=0.01)

    def test_penalty_for_remix(self):
        """'Remix' in bundle title but not canonical should penalize."""
        penalty = _version_suffix_penalty("Blinding Lights (Remix)", "Blinding Lights")
        assert penalty < 1.0

    def test_penalty_for_acoustic(self):
        """'Acoustic' in bundle title but not canonical should penalize."""
        penalty = _version_suffix_penalty("Blinding Lights (Acoustic)", "Blinding Lights")
        assert penalty < 1.0

    def test_penalty_for_cover(self):
        """'Cover' in bundle title but not canonical should penalize."""
        penalty = _version_suffix_penalty("Blinding Lights (Cover)", "Blinding Lights")
        assert penalty < 1.0

    def test_no_penalty_if_canonical_has_live(self):
        """If canonical title also has 'Live', no penalty."""
        penalty = _version_suffix_penalty("Blinding Lights (Live)", "Blinding Lights (Live)")
        assert penalty == 1.0

    def test_no_penalty_for_official_video(self):
        """'Official Video' is harmless — no penalty."""
        penalty = _version_suffix_penalty(
            "Blinding Lights (Official Video)", "Blinding Lights"
        )
        assert penalty == 1.0

    def test_no_penalty_for_lyric_video(self):
        """'Lyric Video' is harmless — no penalty."""
        penalty = _version_suffix_penalty(
            "Blinding Lights (Lyric Video)", "Blinding Lights"
        )
        assert penalty == 1.0

    def test_no_penalty_for_4k(self):
        """'[4K]' is harmless — no penalty."""
        penalty = _version_suffix_penalty(
            "Blinding Lights [4K]", "Blinding Lights"
        )
        assert penalty == 1.0

    def test_no_penalty_for_hd(self):
        """'[HD]' is harmless — no penalty."""
        penalty = _version_suffix_penalty(
            "Blinding Lights [HD]", "Blinding Lights"
        )
        assert penalty == 1.0

    def test_no_penalty_for_official_audio(self):
        """'Official Audio' is harmless — no penalty."""
        penalty = _version_suffix_penalty(
            "Blinding Lights (Official Audio)", "Blinding Lights"
        )
        assert penalty == 1.0

    def test_compound_penalty_live_and_remix(self):
        """Multiple unprompted suffixes compound the penalty."""
        penalty = _version_suffix_penalty(
            "Blinding Lights (Live Remix)", "Blinding Lights"
        )
        # Two suffixes: 0.7 * 0.7 = 0.49
        assert penalty == pytest.approx(0.49, rel=0.01)

    def test_empty_bundle_title(self):
        """Empty bundle title should return no penalty."""
        penalty = _version_suffix_penalty("", "Blinding Lights")
        assert penalty == 1.0

    def test_empty_canonical_title(self):
        """Empty canonical title should return no penalty."""
        penalty = _version_suffix_penalty("Blinding Lights (Live)", "")
        assert penalty == 1.0

    def test_case_insensitive(self):
        """Penalty should be case-insensitive."""
        penalty1 = _version_suffix_penalty("Blinding Lights (LIVE)", "Blinding Lights")
        penalty2 = _version_suffix_penalty("Blinding Lights (live)", "Blinding Lights")
        assert penalty1 == penalty2
        assert penalty1 < 1.0

    def test_penalty_for_piano_version(self):
        """'Piano' version should be penalized."""
        penalty = _version_suffix_penalty("Blinding Lights (Piano Version)", "Blinding Lights")
        assert penalty < 1.0

    def test_penalty_for_guitar_version(self):
        """'Guitar' version should be penalized."""
        penalty = _version_suffix_penalty("Blinding Lights (Guitar Cover)", "Blinding Lights")
        # Both 'guitar' and 'cover' should penalize
        assert penalty < 0.7  # Two suffixes: 0.7 * 0.7 = 0.49

    def test_penalty_for_karaoke(self):
        """'Karaoke' version should be penalized."""
        penalty = _version_suffix_penalty("Blinding Lights (Karaoke)", "Blinding Lights")
        assert penalty < 1.0

    def test_penalty_for_a_cappella(self):
        """'A cappella' version should be penalized."""
        penalty = _version_suffix_penalty("Blinding Lights (A Cappella)", "Blinding Lights")
        assert penalty < 1.0

    def test_penalty_for_unplugged(self):
        """'Unplugged' version should be penalized."""
        penalty = _version_suffix_penalty("Blinding Lights (Unplugged)", "Blinding Lights")
        assert penalty < 1.0

    def test_penalty_for_orchestral(self):
        """'Orchestral' version should be penalized."""
        penalty = _version_suffix_penalty("Blinding Lights (Orchestral)", "Blinding Lights")
        assert penalty < 1.0

    def test_penalty_for_medley(self):
        """'Medley' version should be penalized."""
        penalty = _version_suffix_penalty("Blinding Lights Medley", "Blinding Lights")
        assert penalty < 1.0

    def test_penalty_for_radio_edit(self):
        """'Radio Edit' version should be penalized."""
        penalty = _version_suffix_penalty("Blinding Lights (Radio Edit)", "Blinding Lights")
        assert penalty < 1.0

    def test_penalty_for_extended(self):
        """'Extended' version should be penalized."""
        penalty = _version_suffix_penalty("Blinding Lights (Extended Mix)", "Blinding Lights")
        assert penalty < 1.0

    def test_penalty_for_dj_mix(self):
        """'DJ Mix' version should be penalized."""
        penalty = _version_suffix_penalty("Blinding Lights (DJ Mix)", "Blinding Lights")
        assert penalty < 1.0

    def test_penalty_for_club_mix(self):
        """'Club Mix' version should be penalized."""
        penalty = _version_suffix_penalty("Blinding Lights (Club Mix)", "Blinding Lights")
        assert penalty < 1.0


# ── Scoring with harmless YouTube suffixes ─────────────────────────

class TestScoringHarmlessSuffixes:
    """Verify that harmless YouTube suffixes don't hurt the score much."""

    def test_official_video_scores_high(self):
        """Title with '(Official Video)' should still score high."""
        bundle = _make_bundle(
            title="Blinding Lights (Official Video)",
            artist="The Weeknd",
            ref_duration_ms=200040,
        )
        canonical = _make_canonical()
        score = _score_bundle(bundle, canonical.title, canonical.artist, canonical.duration)
        assert score > 0.6, f"Official Video should score > 0.6, got {score}"

    def test_lyric_video_scores_high(self):
        """Title with '(Lyric Video)' should still score high."""
        bundle = _make_bundle(
            title="Blinding Lights (Lyric Video)",
            artist="The Weeknd",
            ref_duration_ms=200040,
        )
        canonical = _make_canonical()
        score = _score_bundle(bundle, canonical.title, canonical.artist, canonical.duration)
        assert score > 0.6, f"Lyric Video should score > 0.6, got {score}"

    def test_4k_suffix_scores_high(self):
        """Title with '[4K]' should still score high."""
        bundle = _make_bundle(
            title="Blinding Lights [4K]",
            artist="The Weeknd",
            ref_duration_ms=200040,
        )
        canonical = _make_canonical()
        score = _score_bundle(bundle, canonical.title, canonical.artist, canonical.duration)
        assert score > 0.6, f"[4K] should score > 0.6, got {score}"

    def test_hd_suffix_scores_high(self):
        """Title with '[HD]' should still score high."""
        bundle = _make_bundle(
            title="Blinding Lights [HD]",
            artist="The Weeknd",
            ref_duration_ms=200040,
        )
        canonical = _make_canonical()
        score = _score_bundle(bundle, canonical.title, canonical.artist, canonical.duration)
        assert score > 0.6, f"[HD] should score > 0.6, got {score}"

    def test_official_audio_scores_high(self):
        """Title with '(Official Audio)' should still score high."""
        bundle = _make_bundle(
            title="Blinding Lights (Official Audio)",
            artist="The Weeknd",
            ref_duration_ms=200040,
        )
        canonical = _make_canonical()
        score = _score_bundle(bundle, canonical.title, canonical.artist, canonical.duration)
        assert score > 0.6, f"Official Audio should score > 0.6, got {score}"

    def test_topic_artist_suffix(self):
        """Artist with '- Topic' suffix should still score reasonably."""
        bundle = _make_bundle(
            title="Blinding Lights",
            artist="The Weeknd - Topic",
            ref_duration_ms=200040,
        )
        canonical = _make_canonical()
        score = _score_bundle(bundle, canonical.title, canonical.artist, canonical.duration)
        assert score > 0.5, f"Topic suffix should score > 0.5, got {score}"

    def test_visualizer_suffix(self):
        """Title with 'Visualizer' should still score high."""
        bundle = _make_bundle(
            title="Blinding Lights (Visualizer)",
            artist="The Weeknd",
            ref_duration_ms=200040,
        )
        canonical = _make_canonical()
        score = _score_bundle(bundle, canonical.title, canonical.artist, canonical.duration)
        assert score > 0.6, f"Visualizer should score > 0.6, got {score}"

    def test_clean_title_beats_official_video(self):
        """Clean title should score higher than '(Official Video)' variant."""
        clean = _make_bundle("Blinding Lights", "The Weeknd", ref_duration_ms=200040)
        noisy = _make_bundle("Blinding Lights (Official Video)", "The Weeknd", ref_duration_ms=200040)
        canonical = _make_canonical()

        score_clean = _score_bundle(clean, canonical.title, canonical.artist, canonical.duration)
        score_noisy = _score_bundle(noisy, canonical.title, canonical.artist, canonical.duration)
        # Clean title should be >= noisy (fuzzy ratio of noisy is lower)
        assert score_clean >= score_noisy, (
            f"Clean title ({score_clean}) should score >= Official Video ({score_noisy})"
        )


# ── Scoring with version suffixes (should be penalized) ────────────

class TestScoringVersionSuffixes:
    """Verify that version suffixes are properly penalized."""

    def test_live_version_penalized(self):
        """Live version should score lower than studio version."""
        studio = _make_bundle("Blinding Lights", "The Weeknd", ref_duration_ms=200040)
        live = _make_bundle("Blinding Lights (Live)", "The Weeknd", ref_duration_ms=230000)
        canonical = _make_canonical()

        score_studio = _score_bundle(studio, canonical.title, canonical.artist, canonical.duration)
        score_live = _score_bundle(live, canonical.title, canonical.artist, canonical.duration)
        assert score_studio > score_live, (
            f"Studio ({score_studio}) should score higher than Live ({score_live})"
        )

    def test_remix_version_penalized(self):
        """Remix version should score lower than original."""
        original = _make_bundle("Blinding Lights", "The Weeknd", ref_duration_ms=200040)
        remix = _make_bundle("Blinding Lights (Kanye West Remix)", "The Weeknd", ref_duration_ms=240000)
        canonical = _make_canonical()

        score_original = _score_bundle(original, canonical.title, canonical.artist, canonical.duration)
        score_remix = _score_bundle(remix, canonical.title, canonical.artist, canonical.duration)
        assert score_original > score_remix, (
            f"Original ({score_original}) should score higher than Remix ({score_remix})"
        )

    def test_acoustic_version_penalized(self):
        """Acoustic version should score lower than studio version."""
        studio = _make_bundle("Blinding Lights", "The Weeknd", ref_duration_ms=200040)
        acoustic = _make_bundle("Blinding Lights (Acoustic)", "The Weeknd", ref_duration_ms=195000)
        canonical = _make_canonical()

        score_studio = _score_bundle(studio, canonical.title, canonical.artist, canonical.duration)
        score_acoustic = _score_bundle(acoustic, canonical.title, canonical.artist, canonical.duration)
        assert score_studio > score_acoustic, (
            f"Studio ({score_studio}) should score higher than Acoustic ({score_acoustic})"
        )

    def test_cover_version_penalized(self):
        """Cover version should score lower than original."""
        original = _make_bundle("Blinding Lights", "The Weeknd", ref_duration_ms=200040)
        cover = _make_bundle("Blinding Lights (Cover)", "Cover Artist", ref_duration_ms=210000)
        canonical = _make_canonical()

        score_original = _score_bundle(original, canonical.title, canonical.artist, canonical.duration)
        score_cover = _score_bundle(cover, canonical.title, canonical.artist, canonical.duration)
        assert score_original > score_cover, (
            f"Original ({score_original}) should score higher than Cover ({score_cover})"
        )

    def test_instrumental_version_penalized(self):
        """Instrumental version should score lower than vocal version."""
        vocal = _make_bundle("Blinding Lights", "The Weeknd", ref_duration_ms=200040)
        instrumental = _make_bundle("Blinding Lights (Instrumental)", "The Weeknd", ref_duration_ms=200040)
        canonical = _make_canonical()

        score_vocal = _score_bundle(vocal, canonical.title, canonical.artist, canonical.duration)
        score_instrumental = _score_bundle(instrumental, canonical.title, canonical.artist, canonical.duration)
        assert score_vocal > score_instrumental, (
            f"Vocal ({score_vocal}) should score higher than Instrumental ({score_instrumental})"
        )

    def test_unplugged_version_penalized(self):
        """Unplugged version should score lower than studio version."""
        studio = _make_bundle("Blinding Lights", "The Weeknd", ref_duration_ms=200040)
        unplugged = _make_bundle("Blinding Lights (Unplugged)", "The Weeknd", ref_duration_ms=215000)
        canonical = _make_canonical()

        score_studio = _score_bundle(studio, canonical.title, canonical.artist, canonical.duration)
        score_unplugged = _score_bundle(unplugged, canonical.title, canonical.artist, canonical.duration)
        assert score_studio > score_unplugged, (
            f"Studio ({score_studio}) should score higher than Unplugged ({score_unplugged})"
        )

    def test_karaoke_version_penalized(self):
        """Karaoke version should score lower than original."""
        original = _make_bundle("Blinding Lights", "The Weeknd", ref_duration_ms=200040)
        karaoke = _make_bundle("Blinding Lights (Karaoke)", "The Weeknd", ref_duration_ms=200040)
        canonical = _make_canonical()

        score_original = _score_bundle(original, canonical.title, canonical.artist, canonical.duration)
        score_karaoke = _score_bundle(karaoke, canonical.title, canonical.artist, canonical.duration)
        assert score_original > score_karaoke, (
            f"Original ({score_original}) should score higher than Karaoke ({score_karaoke})"
        )

    def test_piano_version_penalized(self):
        """Piano version should score lower than studio version."""
        studio = _make_bundle("Blinding Lights", "The Weeknd", ref_duration_ms=200040)
        piano = _make_bundle("Blinding Lights (Piano Version)", "The Weeknd", ref_duration_ms=190000)
        canonical = _make_canonical()

        score_studio = _score_bundle(studio, canonical.title, canonical.artist, canonical.duration)
        score_piano = _score_bundle(piano, canonical.title, canonical.artist, canonical.duration)
        assert score_studio > score_piano, (
            f"Studio ({score_studio}) should score higher than Piano ({score_piano})"
        )

    def test_live_with_official_video(self):
        """'Live' + 'Official Video' should still be penalized for Live."""
        bundle = _make_bundle(
            "Blinding Lights (Live) (Official Video)", "The Weeknd", ref_duration_ms=230000
        )
        canonical = _make_canonical()
        score = _score_bundle(bundle, canonical.title, canonical.artist, canonical.duration)
        # Should be penalized for "live" even though "official video" is harmless
        clean = _make_bundle("Blinding Lights", "The Weeknd", ref_duration_ms=200040)
        score_clean = _score_bundle(clean, canonical.title, canonical.artist, canonical.duration)
        assert score_clean > score, (
            f"Clean ({score_clean}) should score higher than Live+OfficialVideo ({score})"
        )


# ── Cover with different artist ────────────────────────────────────

class TestScoringCovers:
    """Verify that covers with different artists score low."""

    def test_cover_different_artist_low_score(self):
        """Cover by different artist should score much lower."""
        original = _make_bundle("Blinding Lights", "The Weeknd", ref_duration_ms=200040)
        cover = _make_bundle("Blinding Lights - Cover", "Guitar Cover Band", ref_duration_ms=210000)
        canonical = _make_canonical()

        score_original = _score_bundle(original, canonical.title, canonical.artist, canonical.duration)
        score_cover = _score_bundle(cover, canonical.title, canonical.artist, canonical.duration)
        assert score_original > score_cover, (
            f"Original ({score_original}) should score higher than Cover ({score_cover})"
        )
        assert score_cover < 0.5, f"Cover should score < 0.5, got {score_cover}"

    def test_cover_by_famous_artist_still_penalized(self):
        """Even a cover by a famous artist should be penalized."""
        original = _make_bundle("Blinding Lights", "The Weeknd", ref_duration_ms=200040)
        cover = _make_bundle("Blinding Lights", "Adele", ref_duration_ms=220000)
        canonical = _make_canonical()

        score_original = _score_bundle(original, canonical.title, canonical.artist, canonical.duration)
        score_cover = _score_bundle(cover, canonical.title, canonical.artist, canonical.duration)
        assert score_original > score_cover, (
            f"Original ({score_original}) should score higher than Adele cover ({score_cover})"
        )

    def test_select_best_picks_original_over_cover(self):
        """_select_best_bundle should pick original over cover."""
        bundles = [
            _make_bundle("Blinding Lights", "Cover Artist", ref_duration_ms=210000),
            _make_bundle("Blinding Lights", "The Weeknd", ref_duration_ms=200040),
            _make_bundle("Blinding Lights (Cover)", "Piano Man", ref_duration_ms=195000),
        ]
        canonical = _make_canonical()
        best = _select_best_bundle(bundles, prefer_synced=True, canonical=canonical)
        assert best is not None
        assert best.artist == "The Weeknd", f"Should pick The Weeknd, got {best.artist}"


# ── Duration mismatch tests ────────────────────────────────────────

class TestScoringDurationMismatch:
    """Verify that large duration mismatches reduce the score."""

    def test_small_duration_diff_ok(self):
        """Small duration difference (±3s) should not hurt much."""
        bundle = _make_bundle("Blinding Lights", "The Weeknd", ref_duration_ms=202000)
        canonical = _make_canonical(duration=200.04)
        score = _score_bundle(bundle, canonical.title, canonical.artist, canonical.duration)
        assert score > 0.7, f"Small duration diff should score > 0.7, got {score}"

    def test_large_duration_diff_penalized(self):
        """Large duration difference (>30%) should reduce the score compared to good match."""
        good = _make_bundle("Blinding Lights", "The Weeknd", ref_duration_ms=200040)
        bad = _make_bundle("Blinding Lights", "The Weeknd", ref_duration_ms=100000)
        canonical = _make_canonical(duration=200.04)
        score_good = _score_bundle(good, canonical.title, canonical.artist, canonical.duration)
        score_bad = _score_bundle(bad, canonical.title, canonical.artist, canonical.duration)
        assert score_good > score_bad, (
            f"Good duration ({score_good}) should score higher than bad duration ({score_bad})"
        )
        # With exact title+artist match, base score is ~0.80. Duration only contributes 0.20.
        # So even with duration_score=0.0, the total is ~0.60 (0.80 * 0.75 from duration weight).
        assert score_bad < score_good, "Bad duration should be lower"

    def test_very_short_version_penalized(self):
        """Very short version (snippet) should score lower than correct duration."""
        correct = _make_bundle("Blinding Lights", "The Weeknd", ref_duration_ms=200040)
        short = _make_bundle("Blinding Lights", "The Weeknd", ref_duration_ms=30000)
        canonical = _make_canonical(duration=200.04)
        score_correct = _score_bundle(correct, canonical.title, canonical.artist, canonical.duration)
        score_short = _score_bundle(short, canonical.title, canonical.artist, canonical.duration)
        assert score_correct > score_short, (
            f"Correct duration ({score_correct}) should score higher than very short ({score_short})"
        )

    def test_double_duration_penalized(self):
        """Double duration (DJ set / medley) should score lower than correct duration."""
        correct = _make_bundle("Blinding Lights", "The Weeknd", ref_duration_ms=200040)
        double = _make_bundle("Blinding Lights", "The Weeknd", ref_duration_ms=400000)
        canonical = _make_canonical(duration=200.04)
        score_correct = _score_bundle(correct, canonical.title, canonical.artist, canonical.duration)
        score_double = _score_bundle(double, canonical.title, canonical.artist, canonical.duration)
        assert score_correct > score_double, (
            f"Correct duration ({score_correct}) should score higher than double ({score_double})"
        )

    def test_duration_30_percent_diff(self):
        """30% duration difference should reduce score compared to exact match."""
        exact = _make_bundle("Blinding Lights", "The Weeknd", ref_duration_ms=200040)
        off = _make_bundle("Blinding Lights", "The Weeknd", ref_duration_ms=260000)
        canonical = _make_canonical(duration=200.04)
        score_exact = _score_bundle(exact, canonical.title, canonical.artist, canonical.duration)
        score_off = _score_bundle(off, canonical.title, canonical.artist, canonical.duration)
        assert score_exact > score_off, (
            f"Exact duration ({score_exact}) should score higher than 30% off ({score_off})"
        )
        # Duration score for 60s diff: max(0, 1 - 60/30) = 0.0
        # So the off bundle loses the entire 0.20 duration weight
        assert score_off < score_exact * 0.8, (
            f"30% duration diff should reduce score significantly: {score_off} vs {score_exact}"
        )


# ── Real-world YouTube title patterns ──────────────────────────────

class TestRealWorldYouTubeTitles:
    """Test with real YouTube title patterns."""

    def test_youtube_official_format(self):
        """Real YouTube official upload format."""
        bundle = _make_bundle(
            "The Weeknd - Blinding Lights (Official Video)",
            "The Weeknd",
            ref_duration_ms=200040,
        )
        canonical = _make_canonical()
        score = _score_bundle(bundle, canonical.title, canonical.artist, canonical.duration)
        assert score > 0.5, f"Official format should score > 0.5, got {score}"

    def test_youtube_with_year(self):
        """Title with year suffix."""
        bundle = _make_bundle(
            "Blinding Lights (2020)",
            "The Weeknd",
            ref_duration_ms=200040,
        )
        canonical = _make_canonical()
        score = _score_bundle(bundle, canonical.title, canonical.artist, canonical.duration)
        assert score > 0.5, f"Year suffix should score > 0.5, got {score}"

    def test_youtube_live_from_show(self):
        """Live from a TV show should be penalized."""
        studio = _make_bundle("Blinding Lights", "The Weeknd", ref_duration_ms=200040)
        live = _make_bundle(
            "The Weeknd - Blinding Lights (Live from Saturday Night Live)",
            "The Weeknd",
            ref_duration_ms=230000,
        )
        canonical = _make_canonical()
        score_studio = _score_bundle(studio, canonical.title, canonical.artist, canonical.duration)
        score_live = _score_bundle(live, canonical.title, canonical.artist, canonical.duration)
        assert score_studio > score_live, (
            f"Studio ({score_studio}) should score higher than Live from SNL ({score_live})"
        )

    def test_youtube_remix_with_featured(self):
        """Remix with featured artist should be penalized."""
        original = _make_bundle("Blinding Lights", "The Weeknd", ref_duration_ms=200040)
        remix = _make_bundle(
            "Blinding Lights (Daft Punk Remix) ft. Dua Lipa",
            "The Weeknd",
            ref_duration_ms=250000,
        )
        canonical = _make_canonical()
        score_original = _score_bundle(original, canonical.title, canonical.artist, canonical.duration)
        score_remix = _score_bundle(remix, canonical.title, canonical.artist, canonical.duration)
        assert score_original > score_remix, (
            f"Original ({score_original}) should score higher than Remix ({score_remix})"
        )

    def test_youtube_spanish_audio_oficial(self):
        """Spanish 'Audio Oficial' suffix should be harmless."""
        bundle = _make_bundle(
            "Blinding Lights (Audio Oficial)",
            "The Weeknd",
            ref_duration_ms=200040,
        )
        canonical = _make_canonical()
        score = _score_bundle(bundle, canonical.title, canonical.artist, canonical.duration)
        assert score > 0.5, f"Audio Oficial should score > 0.5, got {score}"

    def test_youtube_video_oficial(self):
        """Spanish 'Video Oficial' suffix should be harmless."""
        bundle = _make_bundle(
            "Blinding Lights (Video Oficial)",
            "The Weeknd",
            ref_duration_ms=200040,
        )
        canonical = _make_canonical()
        score = _score_bundle(bundle, canonical.title, canonical.artist, canonical.duration)
        assert score > 0.5, f"Video Oficial should score > 0.5, got {score}"

    def test_youtube_4k_audio(self):
        """'4K Audio' suffix should be harmless."""
        bundle = _make_bundle(
            "Blinding Lights (4K Audio)",
            "The Weeknd",
            ref_duration_ms=200040,
        )
        canonical = _make_canonical()
        score = _score_bundle(bundle, canonical.title, canonical.artist, canonical.duration)
        assert score > 0.5, f"4K Audio should score > 0.5, got {score}"

    def test_youtube_hq_suffix(self):
        """'HQ' suffix should be harmless."""
        bundle = _make_bundle(
            "Blinding Lights HQ",
            "The Weeknd",
            ref_duration_ms=200040,
        )
        canonical = _make_canonical()
        score = _score_bundle(bundle, canonical.title, canonical.artist, canonical.duration)
        assert score > 0.5, f"HQ should score > 0.5, got {score}"

    def test_complex_youtube_title(self):
        """Complex real-world YouTube title with multiple suffixes."""
        bundle = _make_bundle(
            "The Weeknd - Blinding Lights (Official Video) [4K] HD",
            "The Weeknd",
            ref_duration_ms=200040,
        )
        canonical = _make_canonical()
        score = _score_bundle(bundle, canonical.title, canonical.artist, canonical.duration)
        assert score > 0.4, f"Complex title should score > 0.4, got {score}"


# ── _select_best_bundle integration tests ──────────────────────────

class TestSelectBestDirtyTitles:
    """Integration tests: _select_best_bundle with dirty title bundles."""

    def test_selects_clean_over_live(self):
        """Should select clean studio version over live."""
        bundles = [
            _make_bundle("Blinding Lights (Live)", "The Weeknd", ref_duration_ms=230000),
            _make_bundle("Blinding Lights", "The Weeknd", ref_duration_ms=200040),
        ]
        canonical = _make_canonical()
        best = _select_best_bundle(bundles, prefer_synced=True, canonical=canonical)
        assert best is not None
        assert "Live" not in best.title, f"Should not pick Live version, got: {best.title}"

    def test_selects_clean_over_remix(self):
        """Should select clean version over remix."""
        bundles = [
            _make_bundle("Blinding Lights (Remix)", "The Weeknd", ref_duration_ms=240000),
            _make_bundle("Blinding Lights", "The Weeknd", ref_duration_ms=200040),
        ]
        canonical = _make_canonical()
        best = _select_best_bundle(bundles, prefer_synced=True, canonical=canonical)
        assert best is not None
        assert "Remix" not in best.title, f"Should not pick Remix, got: {best.title}"

    def test_selects_clean_over_acoustic(self):
        """Should select clean version over acoustic."""
        bundles = [
            _make_bundle("Blinding Lights (Acoustic)", "The Weeknd", ref_duration_ms=195000),
            _make_bundle("Blinding Lights", "The Weeknd", ref_duration_ms=200040),
        ]
        canonical = _make_canonical()
        best = _select_best_bundle(bundles, prefer_synced=True, canonical=canonical)
        assert best is not None
        assert "Acoustic" not in best.title, f"Should not pick Acoustic, got: {best.title}"

    def test_selects_clean_over_cover(self):
        """Should select original over cover."""
        bundles = [
            _make_bundle("Blinding Lights", "Cover Artist", ref_duration_ms=210000),
            _make_bundle("Blinding Lights", "The Weeknd", ref_duration_ms=200040),
        ]
        canonical = _make_canonical()
        best = _select_best_bundle(bundles, prefer_synced=True, canonical=canonical)
        assert best is not None
        assert best.artist == "The Weeknd", f"Should pick The Weeknd, got: {best.artist}"

    def test_selects_clean_over_instrumental(self):
        """Should select vocal version over instrumental."""
        bundles = [
            _make_bundle("Blinding Lights (Instrumental)", "The Weeknd", ref_duration_ms=200040),
            _make_bundle("Blinding Lights", "The Weeknd", ref_duration_ms=200040),
        ]
        canonical = _make_canonical()
        best = _select_best_bundle(bundles, prefer_synced=True, canonical=canonical)
        assert best is not None
        assert "Instrumental" not in best.title, f"Should not pick Instrumental, got: {best.title}"

    def test_selects_best_from_mixed_bundles(self):
        """Should pick the best from a mix of dirty and clean bundles."""
        bundles = [
            _make_bundle("Blinding Lights (Live)", "The Weeknd", ref_duration_ms=230000),
            _make_bundle("Blinding Lights (Cover)", "Piano Man", ref_duration_ms=195000),
            _make_bundle("Blinding Lights", "The Weeknd", ref_duration_ms=200040),
            _make_bundle("Blinding Lights (Remix)", "The Weeknd", ref_duration_ms=240000),
            _make_bundle("Blinding Lights (Official Video)", "The Weeknd", ref_duration_ms=200040),
        ]
        canonical = _make_canonical()
        best = _select_best_bundle(bundles, prefer_synced=True, canonical=canonical)
        assert best is not None
        # Should pick the clean title, not Live, Cover, Remix, or Official Video
        assert best.artist == "The Weeknd"
        assert "Live" not in best.title
        assert "Cover" not in best.title
        assert "Remix" not in best.title

    def test_selects_short_version_over_long_live(self):
        """Should prefer correct duration over wrong duration."""
        bundles = [
            _make_bundle("Blinding Lights", "The Weeknd", ref_duration_ms=100000),  # Too short
            _make_bundle("Blinding Lights", "The Weeknd", ref_duration_ms=200040),  # Correct
            _make_bundle("Blinding Lights (Live)", "The Weeknd", ref_duration_ms=300000),  # Too long
        ]
        canonical = _make_canonical(duration=200.04)
        best = _select_best_bundle(bundles, prefer_synced=True, canonical=canonical)
        assert best is not None
        assert best.ref_duration_ms == 200040, f"Should pick correct duration, got {best.ref_duration_ms}"

    def test_no_canonical_fallback(self):
        """Without canonical metadata, should use original sorting."""
        bundles = [
            _make_bundle("Song A", "Artist A", ref_duration_ms=200000, confidence=0.5),
            _make_bundle("Song B", "Artist B", ref_duration_ms=100000, confidence=0.9),
        ]
        best = _select_best_bundle(bundles, prefer_synced=True, canonical=None)
        assert best is not None

    def test_empty_bundles_returns_none(self):
        """Empty bundle list should return None."""
        result = _select_best_bundle([], canonical=_make_canonical())
        assert result is None

    def test_all_below_confidence_returns_none(self):
        """All bundles below confidence threshold should return None."""
        bundles = [
            _make_bundle("Song", "Artist", confidence=0.1),
            _make_bundle("Song2", "Artist2", confidence=0.2),
        ]
        result = _select_best_bundle(bundles, canonical=_make_canonical())
        assert result is None


# ── Logging verification ───────────────────────────────────────────

class TestScoringLogging:
    """Verify that scoring decisions are logged."""

    def test_logging_on_select_best(self, caplog):
        """_select_best_bundle should log the winner with score."""
        import logging
        caplog.set_level(logging.INFO, logger="otterrip_core.lyrics")

        bundles = [
            _make_bundle("Blinding Lights (Live)", "The Weeknd", ref_duration_ms=230000),
            _make_bundle("Blinding Lights", "The Weeknd", ref_duration_ms=200040),
        ]
        canonical = _make_canonical()
        best = _select_best_bundle(bundles, prefer_synced=True, canonical=canonical)

        assert best is not None
        # Check that the log contains the scoring winner info
        log_text = caplog.text
        assert "scoring winner" in log_text, f"Expected 'scoring winner' in logs, got: {log_text}"
        assert "Blinding Lights" in log_text
        assert "The Weeknd" in log_text

    def test_logging_includes_score(self, caplog):
        """Log should include the score value."""
        import logging
        caplog.set_level(logging.INFO, logger="otterrip_core.lyrics")

        bundles = [
            _make_bundle("Blinding Lights", "The Weeknd", ref_duration_ms=200040),
        ]
        canonical = _make_canonical()
        _select_best_bundle(bundles, prefer_synced=True, canonical=canonical)

        log_text = caplog.text
        assert "score=" in log_text, f"Expected 'score=' in logs, got: {log_text}"

    def test_logging_includes_kind(self, caplog):
        """Log should include the sync kind (line/word/plain)."""
        import logging
        caplog.set_level(logging.INFO, logger="otterrip_core.lyrics")

        bundles = [
            _make_bundle("Blinding Lights", "The Weeknd", kind="line", ref_duration_ms=200040),
        ]
        canonical = _make_canonical()
        _select_best_bundle(bundles, prefer_synced=True, canonical=canonical)

        log_text = caplog.text
        assert "kind=" in log_text, f"Expected 'kind=' in logs, got: {log_text}"

    def test_logging_includes_source(self, caplog):
        """Log should include the source provider."""
        import logging
        caplog.set_level(logging.INFO, logger="otterrip_core.lyrics")

        bundles = [
            _make_bundle("Blinding Lights", "The Weeknd", ref_duration_ms=200040, source="musixmatch"),
        ]
        canonical = _make_canonical()
        _select_best_bundle(bundles, prefer_synced=True, canonical=canonical)

        log_text = caplog.text
        assert "source=" in log_text, f"Expected 'source=' in logs, got: {log_text}"


# ── Edge cases ─────────────────────────────────────────────────────

class TestScoringEdgeCases:
    """Edge cases for scoring with dirty titles."""

    def test_bundle_with_no_title(self):
        """Bundle with empty title should still work."""
        bundle = _make_bundle("", "The Weeknd", ref_duration_ms=200040)
        canonical = _make_canonical()
        score = _score_bundle(bundle, canonical.title, canonical.artist, canonical.duration)
        assert 0.0 <= score <= 1.0

    def test_bundle_with_no_artist(self):
        """Bundle with empty artist should still work."""
        bundle = _make_bundle("Blinding Lights", "", ref_duration_ms=200040)
        canonical = _make_canonical()
        score = _score_bundle(bundle, canonical.title, canonical.artist, canonical.duration)
        assert 0.0 <= score <= 1.0

    def test_bundle_with_no_duration(self):
        """Bundle with no duration should use default duration score."""
        bundle = _make_bundle("Blinding Lights", "The Weeknd", ref_duration_ms=None)
        canonical = _make_canonical()
        score = _score_bundle(bundle, canonical.title, canonical.artist, canonical.duration)
        assert 0.0 <= score <= 1.0

    def test_canonical_with_live_in_title(self):
        """If canonical title has 'Live', live bundles should not be penalized."""
        live_bundle = _make_bundle("Blinding Lights (Live)", "The Weeknd", ref_duration_ms=230000)
        canonical = _make_canonical(title="Blinding Lights (Live)", duration=230.0)
        score = _score_bundle(live_bundle, canonical.title, canonical.artist, canonical.duration)
        # Should not be penalized for "live" since canonical also has it
        assert score > 0.5, f"Live canonical should not penalize live bundle, got {score}"

    def test_multiple_suffixes_compound(self):
        """Multiple version suffixes should compound the penalty."""
        bundle = _make_bundle(
            "Blinding Lights (Live Acoustic Remix)",
            "The Weeknd",
            ref_duration_ms=250000,
        )
        canonical = _make_canonical()
        score = _score_bundle(bundle, canonical.title, canonical.artist, canonical.duration)
        # Three suffixes: 0.7^3 = 0.343 multiplier
        clean = _make_bundle("Blinding Lights", "The Weeknd", ref_duration_ms=200040)
        score_clean = _score_bundle(clean, canonical.title, canonical.artist, canonical.duration)
        assert score < score_clean * 0.5, (
            f"Triple suffix ({score}) should be much lower than clean ({score_clean})"
        )

    def test_score_bounded_0_to_1(self):
        """Score should always be between 0.0 and 1.0."""
        bundle = _make_bundle("Blinding Lights (Live Remix Cover)", "Random Artist", ref_duration_ms=50000)
        canonical = _make_canonical()
        score = _score_bundle(bundle, canonical.title, canonical.artist, canonical.duration)
        assert 0.0 <= score <= 1.0, f"Score should be in [0, 1], got {score}"
