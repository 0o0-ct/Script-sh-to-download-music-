#!/usr/bin/env python3
"""Tests for musicbrainz.py — MusicBrainz canonical metadata lookup.

Uses mocked HTTP responses to avoid hitting the real API.
Tests extended metadata fields, rate limiter integration, caching,
and lookup_by_isrc.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from otterrip_core.musicbrainz import (
    CanonicalMetadata,
    _extract_artist_ids,
    _extract_duration,
    _extract_isrc,
    _extract_release_info,
    _join_artists,
    lookup_canonical,
    lookup_by_isrc,
)


# ── Helpers ────────────────────────────────────────────────────────

def _mock_limiter(response_body: dict | None) -> MagicMock:
    """Create a mock MusicBrainzRateLimiter that returns the given body."""
    limiter = MagicMock()
    limiter.fetch_json = AsyncMock(return_value=response_body)
    return limiter


# ── Sample MusicBrainz JSON responses ──────────────────────────────

MB_SUCCESS_RESPONSE = {
    "recordings": [
        {
            "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
            "title": "Blinding Lights",
            "artist-credit": [
                {"name": "The Weeknd", "artist": {"id": "artist-id-1"}}
            ],
            "length": "200040000",  # microseconds = 200.04s
            "isrcs": [{"isrc": "USUG11901213"}],
            "releases": [
                {
                    "title": "After Hours",
                    "id": "release-id-1",
                    "country": "US",
                    "date": "2020-03-20",
                    "p-list": [{"name": "XO / Republic Records"}],
                    "medium-list": [
                        {
                            "position": 1,
                            "track-list": [
                                {
                                    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                                    "number": "3",
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    ],
    "recording-count": 1,
}

MB_NO_RESULTS_RESPONSE = {
    "recordings": [],
    "recording-count": 0,
}

MB_MULTI_ARTIST_RESPONSE = {
    "recordings": [
        {
            "id": "multi-artist-id",
            "title": "Somebody Else",
            "artist-credit": [
                {"name": "Jamie xx", "artist": {"id": "a1"}},
                {"name": "The Weeknd", "artist": {"id": "a2"}},
            ],
            "length": "243000000",
            "isrcs": [],
            "releases": [],
        }
    ],
    "recording-count": 1,
}

MB_NO_ISRC_RESPONSE = {
    "recordings": [
        {
            "id": "no-isrc-id",
            "title": "Old Song",
            "artist-credit": [{"name": "Vintage Artist"}],
            "length": "180000000",
            "isrcs": [],
            "releases": [{"title": "Classic Album"}],
        }
    ],
    "recording-count": 1,
}


# ── Unit tests for helper functions ────────────────────────────────

class TestJoinArtists:
    def test_single_artist(self):
        rec = {"artist-credit": [{"name": "The Weeknd"}]}
        assert _join_artists(rec) == "The Weeknd"

    def test_multiple_artists(self):
        rec = {
            "artist-credit": [
                {"name": "Artist A"},
                {"name": "Artist B"},
            ]
        }
        assert _join_artists(rec) == "Artist A, Artist B"

    def test_empty_credit(self):
        rec = {"artist-credit": []}
        assert _join_artists(rec) == ""

    def test_missing_key(self):
        rec = {}
        assert _join_artists(rec) == ""

    def test_artist_credit_as_strings(self):
        rec = {"artist-credit": ["The Weeknd"]}
        assert _join_artists(rec) == "The Weeknd"

    def test_artist_credit_mixed(self):
        rec = {"artist-credit": [{"name": "Artist A"}, "Artist B"]}
        assert _join_artists(rec) == "Artist A, Artist B"


class TestExtractISRC:
    def test_with_isrc(self):
        rec = {"isrcs": [{"isrc": "USUG11901213"}]}
        assert _extract_isrc(rec) == "USUG11901213"

    def test_no_isrcs(self):
        rec = {"isrcs": []}
        assert _extract_isrc(rec) == ""

    def test_missing_key(self):
        rec = {}
        assert _extract_isrc(rec) == ""

    def test_isrcs_as_string_list(self):
        rec = {"isrcs": ["USUG11901213"]}
        assert _extract_isrc(rec) == "USUG11901213"

    def test_isrcs_as_string_list_with_whitespace(self):
        rec = {"isrcs": ["  USUG11901213  "]}
        assert _extract_isrc(rec) == "USUG11901213"


class TestExtractDuration:
    def test_normal_length(self):
        rec = {"length": 200040000}
        assert _extract_duration(rec) == 200.04

    def test_zero_length(self):
        rec = {"length": 0}
        assert _extract_duration(rec) == 0.0

    def test_missing_length(self):
        rec = {}
        assert _extract_duration(rec) is None

    def test_invalid_length(self):
        rec = {"length": "not-a-number"}
        assert _extract_duration(rec) is None


class TestExtractArtistIds:
    def test_single_artist_id(self):
        rec = {"artist-credit": [{"name": "A", "artist": {"id": "id-1"}}]}
        assert _extract_artist_ids(rec) == ["id-1"]

    def test_multiple_artist_ids(self):
        rec = {
            "artist-credit": [
                {"name": "A", "artist": {"id": "id-1"}},
                {"name": "B", "artist": {"id": "id-2"}},
            ]
        }
        assert _extract_artist_ids(rec) == ["id-1", "id-2"]

    def test_no_artist_ids(self):
        rec = {"artist-credit": [{"name": "A"}]}
        assert _extract_artist_ids(rec) == []

    def test_empty_credit(self):
        rec = {"artist-credit": []}
        assert _extract_artist_ids(rec) == []


class TestExtractReleaseInfo:
    def test_full_release_info(self):
        rec = {
            "id": "rec-id-1",
            "releases": [
                {
                    "title": "After Hours",
                    "id": "release-id-1",
                    "country": "US",
                    "date": "2020-03-20",
                    "p-list": [{"name": "XO / Republic"}],
                    "medium-list": [
                        {
                            "position": 1,
                            "track-list": [
                                {"id": "rec-id-1", "number": "3"},
                            ],
                        }
                    ],
                }
            ],
        }
        info = _extract_release_info(rec)
        assert info["album"] == "After Hours"
        assert info["release_id"] == "release-id-1"
        assert info["release_country"] == "US"
        assert info["year"] == 2020
        assert info["label"] == "XO / Republic"
        assert info["track_number"] == 3
        assert info["disc_number"] == 1

    def test_no_releases(self):
        rec = {"releases": []}
        info = _extract_release_info(rec)
        assert info["album"] == ""
        assert info["track_number"] == 0
        assert info["year"] == 0

    def test_missing_key(self):
        rec = {}
        info = _extract_release_info(rec)
        assert info["album"] == ""


class TestCanonicalMetadata:
    def test_default_values(self):
        meta = CanonicalMetadata()
        assert meta.title == ""
        assert meta.track_number == 0
        assert meta.disc_number == 0
        assert meta.year == 0
        assert meta.label == ""
        assert meta.release_id == ""
        assert meta.artist_ids == []
        assert meta.release_country == ""

    def test_to_dict(self):
        meta = CanonicalMetadata(
            title="Song",
            artist="Artist",
            track_number=5,
            year=2021,
            release_id="rel-123",
            artist_ids=["a1", "a2"],
        )
        d = meta.to_dict()
        assert d["title"] == "Song"
        assert d["track_number"] == 5
        assert d["year"] == 2021
        assert d["release_id"] == "rel-123"
        assert d["artist_ids"] == ["a1", "a2"]

    def test_from_dict(self):
        data = {
            "title": "Song",
            "artist": "Artist",
            "album": "Album",
            "duration": 180.0,
            "isrc": "USXX12345678",
            "recording_id": "rec-123",
            "track_number": 7,
            "disc_number": 2,
            "year": 2019,
            "label": "Label",
            "release_id": "rel-456",
            "artist_ids": ["a1"],
            "release_country": "GB",
        }
        meta = CanonicalMetadata.from_dict(data)
        assert meta.title == "Song"
        assert meta.track_number == 7
        assert meta.disc_number == 2
        assert meta.year == 2019
        assert meta.label == "Label"
        assert meta.release_id == "rel-456"
        assert meta.artist_ids == ["a1"]
        assert meta.release_country == "GB"

    def test_from_dict_extra_fields_ignored(self):
        data = {
            "title": "Song",
            "unknown_field": "should_be_ignored",
        }
        meta = CanonicalMetadata.from_dict(data)
        assert meta.title == "Song"

    def test_roundtrip(self):
        original = CanonicalMetadata(
            title="Song",
            artist="Artist",
            track_number=3,
            year=2020,
            release_id="rel-1",
            artist_ids=["a1", "a2"],
            release_country="US",
        )
        restored = CanonicalMetadata.from_dict(original.to_dict())
        assert restored.title == original.title
        assert restored.track_number == original.track_number
        assert restored.year == original.year
        assert restored.release_id == original.release_id
        assert restored.artist_ids == original.artist_ids
        assert restored.release_country == original.release_country


# ── Async integration tests with mocked rate limiter ────────────────

class TestLookupCanonical:
    @pytest.mark.asyncio
    async def test_successful_lookup(self):
        with patch("otterrip_core.musicbrainz.get_rate_limiter") as mock_gl:
            mock_gl.return_value = _mock_limiter(MB_SUCCESS_RESPONSE)
            with patch("otterrip_core.musicbrainz._try_get_cached", return_value=None):
                with patch("otterrip_core.musicbrainz._try_set_cached"):
                    result = await lookup_canonical(
                        title="Blinding Lights",
                        artist="The Weeknd",
                        album="After Hours",
                    )

            assert result is not None
            assert result.title == "Blinding Lights"
            assert result.artist == "The Weeknd"
            assert result.album == "After Hours"
            assert result.duration == 200.04
            assert result.isrc == "USUG11901213"
            assert result.recording_id == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

    @pytest.mark.asyncio
    async def test_extended_metadata(self):
        with patch("otterrip_core.musicbrainz.get_rate_limiter") as mock_gl:
            mock_gl.return_value = _mock_limiter(MB_SUCCESS_RESPONSE)
            with patch("otterrip_core.musicbrainz._try_get_cached", return_value=None):
                with patch("otterrip_core.musicbrainz._try_set_cached"):
                    result = await lookup_canonical(
                        title="Blinding Lights",
                        artist="The Weeknd",
                    )

            assert result is not None
            assert result.track_number == 3
            assert result.disc_number == 1
            assert result.year == 2020
            assert result.label == "XO / Republic Records"
            assert result.release_id == "release-id-1"
            assert result.release_country == "US"
            assert result.artist_ids == ["artist-id-1"]

    @pytest.mark.asyncio
    async def test_no_results(self):
        with patch("otterrip_core.musicbrainz.get_rate_limiter") as mock_gl:
            mock_gl.return_value = _mock_limiter(MB_NO_RESULTS_RESPONSE)
            with patch("otterrip_core.musicbrainz._try_get_cached", return_value=None):
                result = await lookup_canonical(
                    title="Nonexistent Song",
                    artist="Unknown Artist",
                )
            assert result is None

    @pytest.mark.asyncio
    async def test_multi_artist(self):
        with patch("otterrip_core.musicbrainz.get_rate_limiter") as mock_gl:
            mock_gl.return_value = _mock_limiter(MB_MULTI_ARTIST_RESPONSE)
            with patch("otterrip_core.musicbrainz._try_get_cached", return_value=None):
                with patch("otterrip_core.musicbrainz._try_set_cached"):
                    result = await lookup_canonical(
                        title="Somebody Else",
                        artist="Jamie xx",
                    )

            assert result is not None
            assert result.artist == "Jamie xx, The Weeknd"
            assert result.duration == 243.0
            assert result.isrc == ""
            assert result.artist_ids == ["a1", "a2"]

    @pytest.mark.asyncio
    async def test_no_isrc(self):
        with patch("otterrip_core.musicbrainz.get_rate_limiter") as mock_gl:
            mock_gl.return_value = _mock_limiter(MB_NO_ISRC_RESPONSE)
            with patch("otterrip_core.musicbrainz._try_get_cached", return_value=None):
                with patch("otterrip_core.musicbrainz._try_set_cached"):
                    result = await lookup_canonical(
                        title="Old Song",
                        artist="Vintage Artist",
                    )

            assert result is not None
            assert result.isrc == ""
            assert result.album == "Classic Album"

    @pytest.mark.asyncio
    async def test_limiter_returns_none(self):
        with patch("otterrip_core.musicbrainz.get_rate_limiter") as mock_gl:
            mock_gl.return_value = _mock_limiter(None)
            with patch("otterrip_core.musicbrainz._try_get_cached", return_value=None):
                result = await lookup_canonical(
                    title="Test",
                    artist="Test",
                )
            assert result is None

    @pytest.mark.asyncio
    async def test_empty_title_returns_none(self):
        result = await lookup_canonical(title="", artist="Some Artist")
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_artist_returns_none(self):
        result = await lookup_canonical(title="Some Title", artist="")
        assert result is None

    @pytest.mark.asyncio
    async def test_cached_result(self):
        cached_meta = CanonicalMetadata(
            title="Cached Song",
            artist="Cached Artist",
            year=2021,
        )
        with patch("otterrip_core.musicbrainz._try_get_cached", return_value=cached_meta):
            result = await lookup_canonical(
                title="Cached Song",
                artist="Cached Artist",
            )
        assert result is not None
        assert result.title == "Cached Song"
        assert result.year == 2021

    @pytest.mark.asyncio
    async def test_fallback_to_original_on_empty_canonical(self):
        response = {
            "recordings": [
                {
                    "id": "empty-fields-id",
                    "title": "",
                    "artist-credit": [],
                    "length": None,
                    "isrcs": [],
                    "releases": [],
                }
            ],
            "recording-count": 1,
        }
        with patch("otterrip_core.musicbrainz.get_rate_limiter") as mock_gl:
            mock_gl.return_value = _mock_limiter(response)
            with patch("otterrip_core.musicbrainz._try_get_cached", return_value=None):
                with patch("otterrip_core.musicbrainz._try_set_cached"):
                    result = await lookup_canonical(
                        title="Original Title",
                        artist="Original Artist",
                    )

        assert result is not None
        assert result.title == "Original Title"
        assert result.artist == "Original Artist"


class TestLookupByISRC:
    @pytest.mark.asyncio
    async def test_successful_isrc_lookup(self):
        response = {
            "recordings": [
                {
                    "id": "isrc-rec-id",
                    "title": "ISRC Track",
                    "artist-credit": [{"name": "ISRC Artist", "artist": {"id": "ia1"}}],
                    "length": "150000000",
                    "isrcs": [{"isrc": "USXX12345678"}],
                    "releases": [
                        {
                            "title": "ISRC Album",
                            "id": "isrc-rel-id",
                            "date": "2021-06-15",
                        }
                    ],
                }
            ],
        }
        with patch("otterrip_core.musicbrainz.get_rate_limiter") as mock_gl:
            mock_gl.return_value = _mock_limiter(response)
            with patch("otterrip_core.musicbrainz._try_get_cached", return_value=None):
                with patch("otterrip_core.musicbrainz._try_set_cached"):
                    result = await lookup_by_isrc("USXX12345678")

        assert result is not None
        assert result.title == "ISRC Track"
        assert result.artist == "ISRC Artist"
        assert result.isrc == "USXX12345678"
        assert result.album == "ISRC Album"
        assert result.year == 2021
        assert result.release_id == "isrc-rel-id"

    @pytest.mark.asyncio
    async def test_isrc_no_results(self):
        with patch("otterrip_core.musicbrainz.get_rate_limiter") as mock_gl:
            mock_gl.return_value = _mock_limiter(MB_NO_RESULTS_RESPONSE)
            with patch("otterrip_core.musicbrainz._try_get_cached", return_value=None):
                result = await lookup_by_isrc("INVALID000000")
        assert result is None

    @pytest.mark.asyncio
    async def test_isrc_empty_string(self):
        result = await lookup_by_isrc("")
        assert result is None

    @pytest.mark.asyncio
    async def test_isrc_cached(self):
        cached_meta = CanonicalMetadata(
            title="Cached ISRC",
            isrc="USXX99999999",
        )
        with patch("otterrip_core.musicbrainz._try_get_cached", return_value=cached_meta):
            result = await lookup_by_isrc("USXX99999999")
        assert result is not None
        assert result.title == "Cached ISRC"
