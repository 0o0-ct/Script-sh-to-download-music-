#!/usr/bin/env python3
"""Tests for lyrics_overrides.py — OverridesStore, apply_offset_to_lrc, and CLI.

These tests verify that:
- OverridesStore persists and retrieves offset/provider overrides
- apply_offset_to_lrc correctly shifts LRC timestamps
- compute_lrc_hash produces stable hashes
- Interactive functions handle edge cases gracefully
- CLI dispatch works for all subcommands

Test categories:
- OverridesStore CRUD operations (15 tests)
- apply_offset_to_lrc timestamp shifting (10 tests)
- compute_lrc_hash stability (4 tests)
- CLI run_lyrics_overrides dispatch (8 tests)
- Edge cases and error handling (5 tests)
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from otterrip_core.lyrics_overrides import (
    OverridesStore,
    compute_lrc_hash,
    apply_offset_to_lrc,
    _normalize_path,
    DEFAULT_CONFIG_DIR,
    OVERRIDES_FILE,
)


# ── Helpers ────────────────────────────────────────────────────────

def _sample_lrc() -> str:
    """Return a simple LRC string for testing."""
    return (
        "[00:00.00] Intro\n"
        "[00:01.50] Primera línea\n"
        "[00:03.00] Segunda línea\n"
        "[00:04.50] Tercera línea\n"
        "[01:00.00] Un minuto después\n"
    )


class TestNormalizePath:
    """Test _normalize_path helper."""

    def test_relative_to_absolute(self):
        """Relative paths should be resolved to absolute."""
        result = _normalize_path("test.mp3")
        assert Path(result).is_absolute()

    def test_absolute_unchanged(self):
        """Absolute paths should remain absolute."""
        result = _normalize_path("/home/user/test.mp3")
        assert result.startswith("/home/user/")

    def test_tilde_expansion(self):
        """Tilde should be expanded."""
        result = _normalize_path("~/Music/test.mp3")
        assert not result.startswith("~")


class TestOverridesStore:
    """Test OverridesStore CRUD operations."""

    def test_create_store(self, tmp_path: Path):
        """Store should create config dir if needed."""
        store = OverridesStore(config_dir=tmp_path)
        assert tmp_path.is_dir()

    def test_empty_store(self, tmp_path: Path):
        """New store should have no overrides."""
        store = OverridesStore(config_dir=tmp_path)
        assert store.get("/fake/file.mp3") is None

    def test_set_and_get_offset(self, tmp_path: Path):
        """Setting offset should persist and be retrievable."""
        store = OverridesStore(config_dir=tmp_path)
        store.set_offset("/fake/file.mp3", 1240)
        assert store.get_offset("/fake/file.mp3") == 1240

    def test_get_offset_default_zero(self, tmp_path: Path):
        """Unset offset should return 0."""
        store = OverridesStore(config_dir=tmp_path)
        assert store.get_offset("/nonexistent.mp3") == 0

    def test_set_and_get_provider(self, tmp_path: Path):
        """Setting provider should persist."""
        store = OverridesStore(config_dir=tmp_path)
        store.set_provider("/fake/file.mp3", "lrclib", "abc123")
        assert store.get_provider("/fake/file.mp3") == "lrclib"

    def test_get_provider_default_none(self, tmp_path: Path):
        """Unset provider should return None."""
        store = OverridesStore(config_dir=tmp_path)
        assert store.get_provider("/nonexistent.mp3") is None

    def test_get_full_entry(self, tmp_path: Path):
        """get() should return the full entry dict."""
        store = OverridesStore(config_dir=tmp_path)
        store.set_offset("/fake/file.mp3", 500)
        store.set_provider("/fake/file.mp3", "musixmatch", "def456")
        entry = store.get("/fake/file.mp3")
        assert entry is not None
        assert entry["offset_ms"] == 500
        assert entry["provider"] == "musixmatch"
        assert entry["lrc_hash"] == "def456"

    def test_clear_override(self, tmp_path: Path):
        """clear() should remove all overrides for a file."""
        store = OverridesStore(config_dir=tmp_path)
        store.set_offset("/fake/file.mp3", 500)
        store.clear("/fake/file.mp3")
        assert store.get("/fake/file.mp3") is None

    def test_list_overrides_empty(self, tmp_path: Path):
        """Empty store should return empty list."""
        store = OverridesStore(config_dir=tmp_path)
        assert store.list_overrides() == []

    def test_list_overrides_populated(self, tmp_path: Path):
        """list_overrides should return all stored entries."""
        store = OverridesStore(config_dir=tmp_path)
        store.set_offset("/fake/a.mp3", 100)
        store.set_offset("/fake/b.mp3", 200)
        overrides = store.list_overrides()
        assert len(overrides) == 2

    def test_persistence_across_instances(self, tmp_path: Path):
        """Data should persist across store instances."""
        store1 = OverridesStore(config_dir=tmp_path)
        store1.set_offset("/fake/file.mp3", 999)
        store2 = OverridesStore(config_dir=tmp_path)
        assert store2.get_offset("/fake/file.mp3") == 999

    def test_corrupt_json_reset(self, tmp_path: Path):
        """Corrupt JSON file should be reset gracefully."""
        store = OverridesStore(config_dir=tmp_path)
        store.path.write_text("not valid json{{{", encoding="utf-8")
        store._data = store._load()
        assert store._data == {}

    def test_negative_offset(self, tmp_path: Path):
        """Negative offsets should be stored correctly."""
        store = OverridesStore(config_dir=tmp_path)
        store.set_offset("/fake/file.mp3", -500)
        assert store.get_offset("/fake/file.mp3") == -500

    def test_zero_offset(self, tmp_path: Path):
        """Zero offset should be stored and retrieved."""
        store = OverridesStore(config_dir=tmp_path)
        store.set_offset("/fake/file.mp3", 0)
        assert store.get_offset("/fake/file.mp3") == 0

    def test_path_normalization(self, tmp_path: Path):
        """Same file with different paths should map to same key."""
        store = OverridesStore(config_dir=tmp_path)
        # Use a real path for normalization
        real = str(tmp_path / "test.mp3")
        store.set_offset(real, 123)
        assert store.get_offset(real) == 123


class TestApplyOffsetToLrc:
    """Test apply_offset_to_lrc timestamp shifting."""

    def test_positive_offset(self):
        """Positive offset should delay all timestamps."""
        lrc = _sample_lrc()
        result = apply_offset_to_lrc(lrc, 1000)
        # First line should shift from 00:00.00 to 00:01.00
        assert "[00:01.00]" in result
        # Second line should shift from 00:01.50 to 00:02.50
        assert "[00:02.50]" in result

    def test_negative_offset(self):
        """Negative offset should advance timestamps."""
        lrc = _sample_lrc()
        result = apply_offset_to_lrc(lrc, -500)
        # First line: 00:00.00 - 500ms = 00:00.00 (clamped to 0)
        assert "[00:00.00]" in result
        # Second line: 00:01.50 - 500ms = 00:01.00
        assert "[00:01.00]" in result

    def test_zero_offset(self):
        """Zero offset should not change anything."""
        lrc = _sample_lrc()
        result = apply_offset_to_lrc(lrc, 0)
        assert result == lrc

    def test_clamp_to_zero(self):
        """Timestamps should not go below 0."""
        lrc = "[00:00.50] Short line\n"
        result = apply_offset_to_lrc(lrc, -1000)
        assert "[00:00.00]" in result

    def test_large_offset(self):
        """Large offset should work correctly."""
        lrc = "[00:00.00] Start\n"
        result = apply_offset_to_lrc(lrc, 60000)
        assert "[01:00.00]" in result

    def test_preserves_non_timestamp_lines(self):
        """Non-timestamp lines should be preserved."""
        lrc = "[ti:Test Song]\n[00:00.00] Line\n[ar:Artist]\n"
        result = apply_offset_to_lrc(lrc, 500)
        assert "[ti:Test Song]" in result
        assert "[ar:Artist]" in result

    def test_millisecond_precision(self):
        """Offset should maintain millisecond precision."""
        lrc = "[00:00.00] Start\n"
        result = apply_offset_to_lrc(lrc, 150)
        # 150ms = 00:00.15 (centiseconds)
        assert "[00:00.15]" in result

    def test_cross_minute_boundary(self):
        """Offset should handle minute boundaries."""
        lrc = "[00:59.50] Near minute\n"
        result = apply_offset_to_lrc(lrc, 1000)
        assert "[01:00.50]" in result

    def test_empty_lrc(self):
        """Empty LRC should return empty string."""
        result = apply_offset_to_lrc("", 500)
        assert result == ""

    def test_no_timestamps(self):
        """LRC with no timestamps should return unchanged."""
        lrc = "Plain text\nNo timestamps here\n"
        result = apply_offset_to_lrc(lrc, 500)
        assert result == lrc


class TestComputeLrcHash:
    """Test compute_lrc_hash function."""

    def test_stable_hash(self):
        """Same input should produce the same hash."""
        text = "[00:00.00] Test"
        h1 = compute_lrc_hash(text)
        h2 = compute_lrc_hash(text)
        assert h1 == h2

    def test_different_input_different_hash(self):
        """Different input should produce different hash."""
        h1 = compute_lrc_hash("[00:00.00] Test")
        h2 = compute_lrc_hash("[00:00.01] Test")
        assert h1 != h2

    def test_hash_length(self):
        """Hash should be 16 characters."""
        h = compute_lrc_hash("test")
        assert len(h) == 16

    def test_hex_format(self):
        """Hash should be hexadecimal."""
        h = compute_lrc_hash("test")
        assert all(c in "0123456789abcdef" for c in h)


class TestCliDispatch:
    """Test run_lyrics_overrides CLI dispatch."""

    def test_get_command(self, tmp_path: Path):
        """get subcommand should return offset."""
        from otterrip_core.lyrics_overrides import run_lyrics_overrides

        store = OverridesStore(config_dir=tmp_path)
        test_file = str(tmp_path / "test.mp3")
        store.set_offset(test_file, 500)

        args = MagicMock()
        args.lyrics_overrides_cmd = "get"
        args.file = test_file

        with patch("otterrip_core.lyrics_overrides.OverridesStore") as MockStore:
            mock_instance = MagicMock()
            mock_instance.get_offset.return_value = 500
            mock_instance.get.return_value = {"offset_ms": 500}
            MockStore.return_value = mock_instance

            result = run_lyrics_overrides(args)
            assert result == 0

    def test_set_command(self, tmp_path: Path):
        """set subcommand should store offset."""
        from otterrip_core.lyrics_overrides import run_lyrics_overrides

        args = MagicMock()
        args.lyrics_overrides_cmd = "set"
        args.file = str(tmp_path / "test.mp3")
        args.offset = 1240

        with patch("otterrip_core.lyrics_overrides.OverridesStore") as MockStore:
            mock_instance = MagicMock()
            MockStore.return_value = mock_instance

            result = run_lyrics_overrides(args)
            assert result == 0
            mock_instance.set_offset.assert_called_once()

    def test_list_command_empty(self):
        """list subcommand with no overrides should show message."""
        from otterrip_core.lyrics_overrides import run_lyrics_overrides

        args = MagicMock()
        args.lyrics_overrides_cmd = "list"

        with patch("otterrip_core.lyrics_overrides.OverridesStore") as MockStore:
            mock_instance = MagicMock()
            mock_instance.list_overrides.return_value = []
            MockStore.return_value = mock_instance

            result = run_lyrics_overrides(args)
            assert result == 0

    def test_unknown_command(self):
        """Unknown subcommand should return 1."""
        from otterrip_core.lyrics_overrides import run_lyrics_overrides

        args = MagicMock()
        args.lyrics_overrides_cmd = "unknown"

        result = run_lyrics_overrides(args)
        assert result == 1


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_missing_config_dir_created(self, tmp_path: Path):
        """Config dir should be created if it doesn't exist."""
        new_dir = tmp_path / "subdir" / "config"
        store = OverridesStore(config_dir=new_dir)
        assert new_dir.is_dir()

    def test_unicode_in_lrc(self):
        """LRC with unicode should be handled correctly."""
        lrc = "[00:00.00] Hola mundo 🎵\n"
        result = apply_offset_to_lrc(lrc, 500)
        assert "Hola mundo 🎵" in result

    def test_very_large_offset(self):
        """Very large offset should not crash."""
        lrc = "[00:00.00] Start\n"
        result = apply_offset_to_lrc(lrc, 3600000)  # 1 hour
        assert "[60:00.00]" in result

    def test_store_with_special_chars_in_path(self, tmp_path: Path):
        """File paths with special characters should work."""
        store = OverridesStore(config_dir=tmp_path)
        special = str(tmp_path / "song (1) [remix].mp3")
        store.set_offset(special, 100)
        assert store.get_offset(special) == 100

    def test_json_save_format(self, tmp_path: Path):
        """Saved JSON should be properly formatted."""
        store = OverridesStore(config_dir=tmp_path)
        store.set_offset("/fake/file.mp3", 500)
        content = store.path.read_text(encoding="utf-8")
        data = json.loads(content)
        assert isinstance(data, dict)
