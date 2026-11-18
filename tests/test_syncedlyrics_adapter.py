#!/usr/bin/env python3
"""Tests for syncedlyrics_adapter.py — individual provider cascade.

Tests cover:
- SyncedlyricsProvider initialization and validation
- SOURCE_META configuration
- _search_with_backoff retry logic
- build_syncedlyrics_providers factory
- _build_providers integration with individual sources
- Provider order and "all" expansion
- Multiweb deduplication when individual sources are present
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from otterrip_core.adapters.syncedlyrics_adapter import (
    SOURCE_META,
    SyncedlyricsProvider,
    _search_with_backoff,
    build_syncedlyrics_providers,
)


# ── Source metadata tests ──────────────────────────────────────────

class TestSourceMeta:
    def test_musixmatch_supports_sync_and_lang(self):
        sync, lang = SOURCE_META["musixmatch"]
        assert sync is True
        assert lang is True

    def test_netease_supports_sync_no_lang(self):
        sync, lang = SOURCE_META["netease"]
        assert sync is True
        assert lang is False

    def test_megalobiz_supports_sync_no_lang(self):
        sync, lang = SOURCE_META["megalobiz"]
        assert sync is True
        assert lang is False

    def test_genius_plain_only(self):
        sync, lang = SOURCE_META["genius"]
        assert sync is False
        assert lang is False

    def test_all_expected_sources_present(self):
        expected = {"musixmatch", "netease", "megalobiz", "genius"}
        assert set(SOURCE_META.keys()) == expected


# ── Provider initialization tests ──────────────────────────────────

class TestSyncedlyricsProviderInit:
    def test_valid_source_musixmatch(self):
        p = SyncedlyricsProvider("musixmatch")
        assert p.name == "musixmatch"

    def test_valid_source_netease(self):
        p = SyncedlyricsProvider("netease")
        assert p.name == "netease"

    def test_valid_source_megalobiz(self):
        p = SyncedlyricsProvider("megalobiz")
        assert p.name == "megalobiz"

    def test_valid_source_genius(self):
        p = SyncedlyricsProvider("genius")
        assert p.name == "genius"

    def test_invalid_source_raises(self):
        with pytest.raises(ValueError, match="Unknown syncedlyrics source"):
            SyncedlyricsProvider("unknown_source")

    def test_enabled_by_default(self):
        p = SyncedlyricsProvider("musixmatch")
        assert p.enabled_by_default is True

    def test_no_credentials_required(self):
        p = SyncedlyricsProvider("musixmatch")
        assert p.requires_credentials is False


# ── is_available tests ─────────────────────────────────────────────

class TestIsAvailable:
    def test_available_when_syncedlyrics_installed(self):
        with patch("otterrip_core.adapters.syncedlyrics_adapter.importlib.util.find_spec") as mock:
            mock.return_value = MagicMock()
            p = SyncedlyricsProvider("musixmatch")
            assert p.is_available() is True

    def test_not_available_when_syncedlyrics_missing(self):
        with patch("otterrip_core.adapters.syncedlyrics_adapter.importlib.util.find_spec") as mock:
            mock.return_value = None
            p = SyncedlyricsProvider("musixmatch")
            assert p.is_available() is False


# ── Backoff helper tests ───────────────────────────────────────────

class TestSearchWithBackoff:
    @pytest.mark.asyncio
    async def test_returns_text_on_first_try(self):
        def fake_search(*args, **kwargs):
            return "[00:00.00] Hello"

        result = await _search_with_backoff(
            fake_search, "test", "musixmatch", True, None,
            max_retries=3, backoff_base=0.01,
        )
        assert result == "[00:00.00] Hello"

    @pytest.mark.asyncio
    async def test_retries_on_exception(self):
        call_count = 0

        def flaky_search(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("timeout")
            return "[00:00.00] Retry success"

        result = await _search_with_backoff(
            flaky_search, "test", "musixmatch", True, None,
            max_retries=3, backoff_base=0.01,
        )
        assert result == "[00:00.00] Retry success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_returns_none_after_max_retries(self):
        def always_fail(*args, **kwargs):
            raise ConnectionError("permanent failure")

        result = await _search_with_backoff(
            always_fail, "test", "musixmatch", True, None,
            max_retries=3, backoff_base=0.01,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_none_result(self):
        def returns_none(*args, **kwargs):
            return None

        result = await _search_with_backoff(
            returns_none, "test", "musixmatch", True, None,
            max_retries=1, backoff_base=0.01,
        )
        assert result is None


# ── Factory tests ──────────────────────────────────────────────────

class TestBuildSyncedlyricsProviders:
    def test_returns_empty_when_syncedlyrics_missing(self):
        with patch("otterrip_core.adapters.syncedlyrics_adapter.importlib.util.find_spec") as mock:
            mock.return_value = None
            providers = build_syncedlyrics_providers()
            assert providers == []

    def test_returns_all_sources_when_available(self):
        with patch("otterrip_core.adapters.syncedlyrics_adapter.importlib.util.find_spec") as mock:
            mock.return_value = MagicMock()
            providers = build_syncedlyrics_providers()
            names = [p.name for p in providers]
            assert names == ["musixmatch", "netease", "megalobiz", "genius"]

    def test_order_is_quality_based(self):
        with patch("otterrip_core.adapters.syncedlyrics_adapter.importlib.util.find_spec") as mock:
            mock.return_value = MagicMock()
            providers = build_syncedlyrics_providers()
            # Musixmatch (best sync) comes first, Genius (plain only) last
            assert providers[0].name == "musixmatch"
            assert providers[-1].name == "genius"


# ── Confidence calculation tests ───────────────────────────────────

class TestConfidence:
    def test_musixmatch_base_confidence(self):
        p = SyncedlyricsProvider("musixmatch")
        from otterrip_core.adapters.base import SongQuery
        from otterrip_core.lrc_validator import LrcValidationResult

        query = SongQuery(title="Test", artist="Artist", duration=180.0)
        validation = LrcValidationResult(
            valid=True,
            entries=[(0, "Line 1"), (170000, "Last line")],
            headers={},
            plain="Line 1\nLast line",
            warnings=[],
            reason="",
        )
        conf = p._confidence(query, validation)
        # Base 0.8 (no dur boost since 170s vs 180s = 10s diff > 3s)
        assert 0.7 <= conf <= 0.9

    def test_genius_base_confidence(self):
        p = SyncedlyricsProvider("genius")
        from otterrip_core.adapters.base import SongQuery
        from otterrip_core.lrc_validator import LrcValidationResult

        query = SongQuery(title="Test", artist="Artist", duration=180.0)
        validation = LrcValidationResult(
            valid=True,
            entries=[(0, "Line 1"), (178000, "Last line")],
            headers={},
            plain="Line 1\nLast line",
            warnings=[],
            reason="",
        )
        conf = p._confidence(query, validation)
        # Base 0.5 + 0.1 for duration match (178s vs 180s = 2s diff <= 3s)
        assert 0.5 <= conf <= 0.7

    def test_warnings_reduce_confidence(self):
        p = SyncedlyricsProvider("musixmatch")
        from otterrip_core.adapters.base import SongQuery
        from otterrip_core.lrc_validator import LrcValidationResult

        query = SongQuery(title="Test", artist="Artist", duration=180.0)
        validation = LrcValidationResult(
            valid=True,
            entries=[(0, "Line 1"), (178000, "Last line")],
            headers={},
            plain="Line 1\nLast line",
            warnings=["warning1", "warning2", "warning3"],
            reason="",
        )
        conf = p._confidence(query, validation)
        # Base 0.8 + 0.1 (dur match) - 0.15 (3 warnings * 0.05) = 0.75
        assert conf < 0.9


# ── Integration: _build_providers with new sources ─────────────────

class TestBuildProvidersIntegration:
    def test_all_expands_to_canonical_order(self):
        from otterrip_core.lyrics import _build_providers, PROVIDER_ORDER

        with patch(
            "otterrip_core.adapters.syncedlyrics_adapter.importlib.util.find_spec"
        ) as mock_spec:
            mock_spec.return_value = MagicMock()
            providers = _build_providers("all")
            # Should include lrclib + syncedlyrics sources
            names = [p.name for p in providers]
            assert "lrclib" in names
            # Individual syncedlyrics sources should be present
            synced_names = [n for n in names if n in SOURCE_META]
            assert "musixmatch" in synced_names

    def test_individual_source_musixmatch(self):
        from otterrip_core.lyrics import _build_providers

        with patch(
            "otterrip_core.adapters.syncedlyrics_adapter.importlib.util.find_spec"
        ) as mock_spec:
            mock_spec.return_value = MagicMock()
            providers = _build_providers("musixmatch")
            names = [p.name for p in providers]
            assert "musixmatch" in names

    def test_multiple_individual_sources(self):
        from otterrip_core.lyrics import _build_providers

        with patch(
            "otterrip_core.adapters.syncedlyrics_adapter.importlib.util.find_spec"
        ) as mock_spec:
            mock_spec.return_value = MagicMock()
            providers = _build_providers("lrclib,musixmatch,genius")
            names = [p.name for p in providers]
            assert "lrclib" in names
            assert "musixmatch" in names
            assert "genius" in names

    def test_provider_order_respected(self):
        from otterrip_core.lyrics import _build_providers

        with patch(
            "otterrip_core.adapters.syncedlyrics_adapter.importlib.util.find_spec"
        ) as mock_spec:
            mock_spec.return_value = MagicMock()
            # Request in random order
            providers = _build_providers("genius,lrclib,musixmatch")
            names = [p.name for p in providers]
            # lrclib should come before musixmatch
            lrclib_idx = names.index("lrclib")
            musix_idx = names.index("musixmatch")
            assert lrclib_idx < musix_idx

    def test_multiweb_skipped_with_individual_sources(self):
        from otterrip_core.lyrics import _build_providers

        with patch(
            "otterrip_core.adapters.syncedlyrics_adapter.importlib.util.find_spec"
        ) as mock_spec:
            mock_spec.return_value = MagicMock()
            providers = _build_providers("multiweb,musixmatch")
            names = [p.name for p in providers]
            # musixmatch is present, multiweb should be skipped
            assert "musixmatch" in names
            assert "multiweb" not in names

    def test_multiweb_used_when_no_individual_sources(self):
        from otterrip_core.lyrics import _build_providers

        with patch(
            "otterrip_core.adapters.syncedlyrics_adapter.importlib.util.find_spec"
        ) as mock_spec:
            mock_spec.return_value = MagicMock()
            providers = _build_providers("multiweb")
            names = [p.name for p in providers]
            assert "multiweb" in names

    def test_lrclib_always_first(self):
        from otterrip_core.lyrics import _build_providers

        with patch(
            "otterrip_core.adapters.syncedlyrics_adapter.importlib.util.find_spec"
        ) as mock_spec:
            mock_spec.return_value = MagicMock()
            providers = _build_providers("lrclib,musixmatch,netease")
            assert providers[0].name == "lrclib"
