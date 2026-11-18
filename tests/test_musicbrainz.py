#!/usr/bin/env python3
"""Tests for musicbrainz.py — MusicBrainz canonical metadata lookup.

Uses mocked HTTP responses to avoid hitting the real API.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from otterrip_core.musicbrainz import (
    CanonicalMetadata,
    _extract_duration,
    _extract_isrc,
    _join_artists,
    lookup_canonical,
)


# ── Helpers ────────────────────────────────────────────────────────

def _mock_response(status: int, body: dict) -> MagicMock:
    """Create a mock aiohttp response context manager."""
    resp = MagicMock()
    resp.status = status
    resp.json = AsyncMock(return_value=body)
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _make_session(mock_resp: MagicMock) -> MagicMock:
    """Create a mock aiohttp.ClientSession with the given response."""
    session = MagicMock()
    session.get = MagicMock(return_value=mock_resp)
    session.close = AsyncMock()
    return session


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
                {"title": "After Hours", "id": "release-id-1"}
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
        """MusicBrainz may return artist-credit as plain strings in some responses."""
        rec = {"artist-credit": ["The Weeknd"]}
        assert _join_artists(rec) == "The Weeknd"

    def test_artist_credit_mixed(self):
        """Mixed dict and string entries."""
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
        """MusicBrainz search results may return isrcs as plain strings."""
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


# ── Async integration tests with mocked HTTP ───────────────────────

@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


class TestLookupCanonical:
    @pytest.mark.asyncio
    async def test_successful_lookup(self):
        mock_resp = _mock_response(200, MB_SUCCESS_RESPONSE)
        session = _make_session(mock_resp)

        result = await lookup_canonical(
            title="Blinding Lights",
            artist="The Weeknd",
            album="After Hours",
            session=session,
        )

        assert result is not None
        assert result.title == "Blinding Lights"
        assert result.artist == "The Weeknd"
        assert result.album == "After Hours"
        assert result.duration == 200.04
        assert result.isrc == "USUG11901213"
        assert result.recording_id == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"

    @pytest.mark.asyncio
    async def test_no_results(self):
        mock_resp = _mock_response(200, MB_NO_RESULTS_RESPONSE)
        session = _make_session(mock_resp)

        result = await lookup_canonical(
            title="Nonexistent Song",
            artist="Unknown Artist",
            session=session,
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_multi_artist(self):
        mock_resp = _mock_response(200, MB_MULTI_ARTIST_RESPONSE)
        session = _make_session(mock_resp)

        result = await lookup_canonical(
            title="Somebody Else",
            artist="Jamie xx",
            session=session,
        )

        assert result is not None
        assert result.artist == "Jamie xx, The Weeknd"
        assert result.duration == 243.0
        assert result.isrc == ""

    @pytest.mark.asyncio
    async def test_no_isrc(self):
        mock_resp = _mock_response(200, MB_NO_ISRC_RESPONSE)
        session = _make_session(mock_resp)

        result = await lookup_canonical(
            title="Old Song",
            artist="Vintage Artist",
            session=session,
        )

        assert result is not None
        assert result.isrc == ""
        assert result.album == "Classic Album"

    @pytest.mark.asyncio
    async def test_http_error(self):
        mock_resp = _mock_response(500, {})
        session = _make_session(mock_resp)

        result = await lookup_canonical(
            title="Test",
            artist="Test",
            session=session,
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_empty_title_returns_none(self):
        result = await lookup_canonical(
            title="",
            artist="Some Artist",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_artist_returns_none(self):
        result = await lookup_canonical(
            title="Some Title",
            artist="",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_timeout_returns_none(self):
        # Simulate timeout by making the session.get raise TimeoutError
        session = MagicMock()
        session.get = MagicMock(
            side_effect=asyncio.TimeoutError()
        )
        session.close = AsyncMock()

        result = await lookup_canonical(
            title="Test",
            artist="Test",
            timeout=0.001,
            session=session,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_fallback_to_original_on_empty_canonical(self):
        # MusicBrainz returns empty title/artist — should fall back to originals
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
        mock_resp = _mock_response(200, response)
        session = _make_session(mock_resp)

        result = await lookup_canonical(
            title="Original Title",
            artist="Original Artist",
            session=session,
        )

        assert result is not None
        assert result.title == "Original Title"
        assert result.artist == "Original Artist"
