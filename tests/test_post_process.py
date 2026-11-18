#!/usr/bin/env python3
"""Tests for post_process.py — post-processing pipeline module."""
import json
import sys
import tempfile
from pathlib import Path
from unittest import mock

# Ensure lib/ is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from otterrip_core.post_process import (
    _find_audio_files,
    _read_session_files,
    _get_tags,
    post_process_single,
    run_post_process,
    AUDIO_EXTS,
)


class TestFindAudioFiles:
    """Test _find_audio_files helper."""

    def test_single_file(self, tmp_path):
        f = tmp_path / "track.mp3"
        f.touch()
        assert _find_audio_files(f) == [f]

    def test_non_audio_file(self, tmp_path):
        f = tmp_path / "track.txt"
        f.touch()
        assert _find_audio_files(f) == []

    def test_directory(self, tmp_path):
        (tmp_path / "a.mp3").touch()
        (tmp_path / "b.flac").touch()
        (tmp_path / "c.txt").touch()
        files = _find_audio_files(tmp_path)
        assert len(files) == 2

    def test_opus_included(self, tmp_path):
        (tmp_path / "a.opus").touch()
        files = _find_audio_files(tmp_path)
        assert len(files) == 1

    def test_nonexistent(self, tmp_path):
        assert _find_audio_files(tmp_path / "nope") == []


class TestReadSessionFiles:
    """Test _read_session_files parser."""

    def test_json_lines(self, tmp_path):
        f1 = tmp_path / "track1.mp3"
        f1.touch()
        session = tmp_path / "session.json"
        session.write_text(json.dumps({"file": str(f1)}) + "\n")
        files = _read_session_files(str(session))
        assert len(files) == 1
        assert files[0] == f1

    def test_plain_paths(self, tmp_path):
        f1 = tmp_path / "track1.mp3"
        f1.touch()
        session = tmp_path / "session.txt"
        session.write_text(str(f1) + "\n")
        files = _read_session_files(str(session))
        assert len(files) == 1

    def test_empty_session(self, tmp_path):
        session = tmp_path / "empty.txt"
        session.write_text("")
        files = _read_session_files(str(session))
        assert files == []

    def test_nonexistent_session(self):
        files = _read_session_files("/nonexistent/session.json")
        assert files == []


class TestGetTags:
    """Test _get_tags reader."""

    def test_no_mutagen(self, tmp_path):
        f = tmp_path / "track.mp3"
        f.touch()
        with mock.patch("mutagen.File", return_value=None):
            tags = _get_tags(f)
        assert tags == {}

    def test_empty_tags(self, tmp_path):
        f = tmp_path / "track.mp3"
        f.touch()
        mock_audio = mock.Mock()
        mock_audio.tags = None
        with mock.patch("mutagen.File", return_value=mock_audio):
            tags = _get_tags(f)
        assert tags == {}


class TestPostProcessSingle:
    """Test post_process_single pipeline."""

    def test_filename_fallback(self, tmp_path):
        f = tmp_path / "Artist - Title.mp3"
        f.touch()
        mock_audio = mock.Mock()
        mock_audio.tags = None

        with mock.patch("mutagen.File", return_value=mock_audio):
            with mock.patch("otterrip_core.musicbrainz.lookup_canonical_sync", return_value=None):
                mock_set_tags = mock.Mock()
                with mock.patch("otterrip_core.fix_metadata._set_tags", mock_set_tags):
                    result = post_process_single(f, lyrics=False)

        assert result["title"] == "Title"
        assert result["artist"] == "Artist"

    def test_keep_thumbnail_skips_cover_art(self, tmp_path):
        f = tmp_path / "Artist - Title.mp3"
        f.touch()
        mock_audio = mock.Mock()
        mock_audio.tags = None

        with mock.patch("mutagen.File", return_value=mock_audio):
            with mock.patch("otterrip_core.musicbrainz.lookup_canonical_sync", return_value=None):
                mock_set_tags = mock.Mock()
                with mock.patch("otterrip_core.fix_metadata._set_tags", mock_set_tags):
                    result = post_process_single(f, keep_thumbnail=True, lyrics=False)

        assert result["cover_art"] == "kept_thumbnail"

    def test_mb_lookup_used(self, tmp_path):
        f = tmp_path / "track.mp3"
        f.touch()

        mb_meta = mock.Mock(
            title="Canonical Title",
            artist="Canonical Artist",
            album="Album",
            release_id="release-123",
            track_number=1,
        )

        # Mock _get_tags to return title/artist directly
        with mock.patch("otterrip_core.post_process._get_tags", return_value={"TIT2": "Test Title", "TPE1": "Test Artist"}):
            with mock.patch("otterrip_core.musicbrainz.lookup_canonical_sync", return_value=mb_meta):
                mock_set_tags = mock.Mock()
                with mock.patch("otterrip_core.fix_metadata._set_tags", mock_set_tags):
                    result = post_process_single(f, lyrics=False)

        assert result["mb_lookup"] is True
        assert result["mb_title"] == "Canonical Title"

    def test_lyrics_disabled(self, tmp_path):
        f = tmp_path / "track.mp3"
        f.touch()
        mock_audio = mock.Mock()
        mock_audio.tags = None

        with mock.patch("mutagen.File", return_value=mock_audio):
            with mock.patch("otterrip_core.musicbrainz.lookup_canonical_sync", return_value=None):
                mock_set_tags = mock.Mock()
                with mock.patch("otterrip_core.fix_metadata._set_tags", mock_set_tags):
                    result = post_process_single(f, lyrics=False)

        assert result["lyrics"] is False


class TestRunPostProcess:
    """Test run_post_process CLI handler."""

    def test_nonexistent_target(self):
        args = mock.Mock(target="/nonexistent", keep_thumbnail=False, session="")
        result = run_post_process(args)
        assert result == 1

    def test_no_audio_files(self, tmp_path):
        args = mock.Mock(target=str(tmp_path), keep_thumbnail=False, session="")
        result = run_post_process(args)
        assert result == 0

    def test_with_session(self, tmp_path):
        f1 = tmp_path / "track1.mp3"
        f1.touch()
        session = tmp_path / "session.json"
        session.write_text(json.dumps({"file": str(f1)}) + "\n")

        mock_audio = mock.Mock()
        mock_audio.tags = None

        args = mock.Mock(
            target=str(tmp_path),
            keep_thumbnail=False,
            session=str(session),
        )
        with mock.patch("mutagen.File", return_value=mock_audio):
            with mock.patch("otterrip_core.musicbrainz.lookup_canonical_sync", return_value=None):
                mock_set_tags = mock.Mock()
                with mock.patch("otterrip_core.fix_metadata._set_tags", mock_set_tags):
                    result = run_post_process(args)

        assert result == 0

    def test_audio_exts_includes_opus(self):
        assert ".opus" in AUDIO_EXTS
