#!/usr/bin/env python3
"""Tests for playlist_manager.py."""
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from otterrip_core.playlist_manager import (
    PlaylistManager,
    _normalize,
    _read_tags,
    _extract_playlist,
    run_playlist,
    AUDIO_EXTS,
)


class TestNormalize:
    def test_lowercase_and_strip(self):
        assert _normalize("  Hello World  ") == "hello world"

    def test_remove_punctuation(self):
        assert _normalize("Hello, World!") == "hello world"

    def test_collapse_spaces(self):
        assert _normalize("Hello   World") == "hello world"

    def test_empty_string(self):
        assert _normalize("") == ""

    def test_special_chars(self):
        assert _normalize("Canción #1 (Official)") == "canción 1 official"


class TestReadTags:
    def test_nonexistent_file(self):
        tags = _read_tags(Path("/nonexistent/file.mp3"))
        assert tags["file"] == "/nonexistent/file.mp3"
        assert tags["name"] == "file.mp3"

    def test_audio_exts_includes_opus(self):
        assert ".opus" in AUDIO_EXTS


class TestPlaylistManager:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.manager = PlaylistManager(self.tmpdir)

    def test_init_default_dest(self):
        m = PlaylistManager()
        assert m.dest == Path.cwd()

    def test_init_custom_dest(self):
        m = PlaylistManager("/tmp/test")
        assert m.dest == Path("/tmp/test")

    def test_scan_local_empty(self):
        result = self.manager.scan_local(self.tmpdir)
        assert result == []

    def test_scan_local_nonexistent(self):
        result = self.manager.scan_local("/nonexistent/path")
        assert result == []

    def test_list_local_empty(self):
        result = self.manager.list_local(self.tmpdir)
        assert result == []

    def test_export_creates_m3u(self):
        """Test export with mocked yt-dlp."""
        mock_entries = [
            {"title": "Song 1", "artist": "Artist A", "url": "https://youtube.com/watch?v=1",
             "id": "1", "duration": 180},
            {"title": "Song 2", "artist": "Artist B", "url": "https://youtube.com/watch?v=2",
             "id": "2", "duration": 200},
        ]
        with patch("otterrip_core.playlist_manager._extract_playlist", return_value=mock_entries):
            m3u_path = self.manager.export("https://youtube.com/playlist?list=test", self.tmpdir)

        assert m3u_path
        m3u_file = Path(m3u_path)
        assert m3u_file.exists()
        content = m3u_file.read_text(encoding="utf-8")
        assert "#EXTM3U" in content
        assert "Song 1" in content
        assert "Artist A" in content
        assert "https://youtube.com/watch?v=1" in content

    def test_export_empty_playlist(self):
        with patch("otterrip_core.playlist_manager._extract_playlist", return_value=[]):
            result = self.manager.export("https://youtube.com/playlist?list=empty", self.tmpdir)
        assert result == ""

    def test_sync_no_local_files(self):
        """Test sync when no local files exist."""
        mock_entries = [
            {"title": "Song 1", "artist": "Artist A", "url": "https://youtube.com/watch?v=1",
             "id": "1", "duration": 180},
        ]
        with patch("otterrip_core.playlist_manager._extract_playlist", return_value=mock_entries):
            result = self.manager.sync("https://youtube.com/playlist?list=test", self.tmpdir)

        assert len(result["new"]) == 1
        assert len(result["removed"]) == 0
        assert len(result["unchanged"]) == 0

    def test_sync_with_matching_local_files(self):
        """Test sync when local files match remote playlist."""
        # Create a dummy file
        dummy = Path(self.tmpdir) / "test.txt"
        dummy.write_text("dummy", encoding="utf-8")

        mock_entries = [
            {"title": "Song 1", "artist": "Artist A", "url": "https://youtube.com/watch?v=1",
             "id": "1", "duration": 180},
        ]
        with patch("otterrip_core.playlist_manager._extract_playlist", return_value=mock_entries):
            with patch.object(self.manager, "scan_local", return_value=[]):
                result = self.manager.sync("https://youtube.com/playlist?list=test", self.tmpdir)

        assert len(result["new"]) == 1

    def test_sync_empty_playlist(self):
        with patch("otterrip_core.playlist_manager._extract_playlist", return_value=[]):
            result = self.manager.sync("https://youtube.com/playlist?list=empty", self.tmpdir)
        assert result == {"new": [], "removed": [], "unchanged": []}


class TestRunPlaylist:
    def test_no_command(self):
        args = MagicMock()
        args.playlist_cmd = None
        args.dest = ""
        result = run_playlist(args)
        assert result == 1

    def test_export_no_url(self):
        args = MagicMock()
        args.playlist_cmd = "export"
        args.url = ""
        args.dest = ""
        result = run_playlist(args)
        assert result == 1

    def test_sync_no_url(self):
        args = MagicMock()
        args.playlist_cmd = "sync"
        args.url = ""
        args.dest = ""
        args.json = False
        result = run_playlist(args)
        assert result == 1

    def test_list_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            args = MagicMock()
            args.playlist_cmd = "list"
            args.dest = tmpdir
            args.json = False
            result = run_playlist(args)
            assert result == 0
