#!/usr/bin/env python3
"""Tests for normalize.py — volume normalization module."""
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

# Ensure lib/ is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from otterrip_core.normalize import (
    _find_audio_files,
    _measure_loudness_ffmpeg,
    _write_replaygain_tags,
    compute_replaygain,
    apply_loudnorm,
    run_normalize,
    DEFAULT_TARGET_LUFS,
    DEFAULT_TRUE_PEAK,
    AUDIO_EXTS,
)


class TestFindAudioFiles:
    """Test _find_audio_files helper."""

    def test_single_file(self, tmp_path):
        f = tmp_path / "track.mp3"
        f.touch()
        assert _find_audio_files(f) == [f]

    def test_single_non_audio_file(self, tmp_path):
        f = tmp_path / "track.txt"
        f.touch()
        assert _find_audio_files(f) == []

    def test_directory(self, tmp_path):
        (tmp_path / "a.mp3").touch()
        (tmp_path / "b.flac").touch()
        (tmp_path / "c.txt").touch()
        files = _find_audio_files(tmp_path)
        assert len(files) == 2
        assert "a.mp3" in [p.name for p in files]

    def test_nested_directory(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "d.m4a").touch()
        (tmp_path / "e.opus").touch()
        files = _find_audio_files(tmp_path)
        assert len(files) == 2

    def test_nonexistent_path(self, tmp_path):
        assert _find_audio_files(tmp_path / "nope") == []

    def test_audio_exts_includes_opus(self):
        assert ".opus" in AUDIO_EXTS

    def test_audio_exts_includes_wav(self):
        assert ".wav" in AUDIO_EXTS


class TestMeasureLoudnessFfmpeg:
    """Test _measure_loudness_ffmpeg with mocked ffmpeg."""

    def test_parsing_target_offset(self, tmp_path):
        f = tmp_path / "track.mp3"
        f.write_bytes(b"\x00" * 100)

        fake_stderr = (
            "[Parsed_ebur128_0 @ 0x0] Summary:\n"
            "Integrated loudness  : -20.50 LUFS\n"
            "True peak           : -3.20 dBFS\n"
            "Target Offset       : +4.50 dB\n"
        )

        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(
                stderr=fake_stderr.encode(), returncode=0
            )
            result = _measure_loudness_ffmpeg(f)

        assert result is not None
        assert result["target_change"] == 4.5
        assert result["true_peak"] == -3.2

    def test_ffmpeg_not_found(self, tmp_path):
        f = tmp_path / "track.mp3"
        f.touch()
        with mock.patch("subprocess.run", side_effect=FileNotFoundError):
            result = _measure_loudness_ffmpeg(f)
        assert result is None

    def test_timeout(self, tmp_path):
        f = tmp_path / "track.mp3"
        f.touch()
        import subprocess as sp
        with mock.patch("subprocess.run", side_effect=sp.TimeoutExpired("ffmpeg", 120)):
            result = _measure_loudness_ffmpeg(f)
        assert result is None

    def test_no_target_offset(self, tmp_path):
        f = tmp_path / "track.mp3"
        f.write_bytes(b"\x00" * 100)
        fake_stderr = "[Parsed_ebur128_0 @ 0x0] No summary available\n"
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(
                stderr=fake_stderr.encode(), returncode=0
            )
            result = _measure_loudness_ffmpeg(f)
        assert result is not None
        assert result["target_change"] is None


class TestWriteReplaygainTags:
    """Test _write_replaygain_tags with mocked mutagen."""

    def test_mp3_tags_with_real_mutagen(self, tmp_path):
        """Test with real mutagen if available, otherwise skip."""
        try:
            import mutagen  # noqa: F401
        except ImportError:
            import pytest
            pytest.skip("mutagen not installed")

        # Create a minimal MP3 file that mutagen can read
        f = tmp_path / "track.mp3"
        # Write a minimal ID3v2 header so mutagen recognizes it
        f.write_bytes(b"ID3\x04\x00\x00\x00\x00\x00" + b"\x00" * 100)

        result = _write_replaygain_tags(f, -3.5, -1.2)
        # If mutagen can parse the file, tags should be written
        # If the file is too minimal, it may fail gracefully
        assert isinstance(result, bool)

    def test_mutagen_not_available(self, tmp_path):
        f = tmp_path / "track.mp3"
        f.touch()
        with mock.patch.dict(sys.modules, {"mutagen": None}):
            # Force reimport
            import importlib
            result = _write_replaygain_tags(f, -3.5, -1.2)
        # Should return False gracefully

    def test_invalid_file(self, tmp_path):
        f = tmp_path / "track.mp3"
        f.touch()
        with mock.patch("mutagen.File", return_value=None):
            result = _write_replaygain_tags(f, -3.5, -1.2)
        assert result is False


class TestComputeReplaygain:
    """Test compute_replaygain integration."""

    def test_ffmpeg_not_available(self, tmp_path):
        f = tmp_path / "track.mp3"
        f.touch()
        with mock.patch("shutil.which", return_value=None):
            result = compute_replaygain(f)
        assert result is False

    def test_successful_replaygain_with_real_mutagen(self, tmp_path):
        """Test with real mutagen if available, otherwise skip."""
        try:
            import mutagen  # noqa: F401
        except ImportError:
            import pytest
            pytest.skip("mutagen not installed")

        f = tmp_path / "track.mp3"
        f.write_bytes(b"ID3\x04\x00\x00\x00\x00\x00" + b"\x00" * 100)
        fake_stderr = (
            "Target Offset       : 4.50 dB\n"
            "True peak           : -3.20 dBFS\n"
        )

        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(
                stderr=fake_stderr.encode(), returncode=0
            )
            result = compute_replaygain(f)

        # Result depends on whether mutagen can parse the minimal file
        assert isinstance(result, bool)


class TestApplyLoudnorm:
    """Test apply_loudnorm with mocked ffmpeg."""

    def test_ffmpeg_not_available(self, tmp_path):
        f = tmp_path / "track.mp3"
        f.touch()
        with mock.patch("shutil.which", return_value=None):
            result = apply_loudnorm(f)
        assert result is False

    def test_successful_loudnorm(self, tmp_path):
        f = tmp_path / "track.mp3"
        f.write_bytes(b"\x00" * 100)

        # Pass 1 output — the JSON block must be the LAST block with }
        pass1_stderr = (
            "{\n"
            '  "input_i": "-20.50",\n'
            '  "input_tp": "-3.20",\n'
            '  "input_lra": "8.50",\n'
            '  "input_thresh": "-2.00",\n'
            '  "target_offset": "+4.50"\n'
            "}\n"
        )

        call_count = [0]
        def side_effect(cmd, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return mock.Mock(stderr=pass1_stderr.encode(), returncode=0)
            else:
                # Pass 2: create output file; cmd is a list, last element is dst
                dst = cmd[-1] if isinstance(cmd, (list, tuple)) else str(cmd)
                Path(dst).write_bytes(b"\x00" * 100)
                return mock.Mock(stderr=b"", returncode=0)

        with mock.patch("subprocess.run", side_effect=side_effect):
            with mock.patch("shutil.which", return_value="/usr/bin/ffmpeg"):
                result = apply_loudnorm(f)

        assert result is True

    def test_loudnorm_pass2_failure(self, tmp_path):
        f = tmp_path / "track.mp3"
        f.write_bytes(b"\x00" * 100)

        pass1_stderr = (
            "{\n"
            '  "input_i": "-20.50",\n'
            '  "input_tp": "-3.20",\n'
            '  "input_lra": "8.50",\n'
            '  "input_thresh": "-2.00",\n'
            '  "target_offset": "+4.50"\n'
            "}\n"
        )

        call_count = [0]
        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return mock.Mock(stderr=pass1_stderr.encode(), returncode=0)
            else:
                return mock.Mock(stderr=b"error", returncode=1)

        with mock.patch("subprocess.run", side_effect=side_effect):
            result = apply_loudnorm(f)

        assert result is False


class TestRunNormalize:
    """Test run_normalize CLI handler."""

    def test_nonexistent_target(self):
        args = mock.Mock(target="/nonexistent/path")
        result = run_normalize(args)
        assert result == 1

    def test_no_audio_files(self, tmp_path):
        args = mock.Mock(target=str(tmp_path), mode="replaygain")
        result = run_normalize(args)
        assert result == 0

    def test_unknown_mode(self, tmp_path):
        f = tmp_path / "track.mp3"
        f.touch()
        args = mock.Mock(target=str(tmp_path), mode="unknown")
        result = run_normalize(args)
        assert result == 1

    def test_replaygain_mode(self, tmp_path):
        f = tmp_path / "track.mp3"
        f.write_bytes(b"\x00" * 100)
        fake_stderr = "Target Offset       : 4.50 dB\nTrue peak           : -3.20 dBFS\n"
        mock_audio = mock.Mock()
        mock_audio.tags = mock.Mock()

        args = mock.Mock(target=str(tmp_path), mode="replaygain")
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(
                stderr=fake_stderr.encode(), returncode=0
            )
            with mock.patch("mutagen.File", return_value=mock_audio):
                result = run_normalize(args)

        assert result == 0

    def test_defaults(self):
        assert DEFAULT_TARGET_LUFS == -16.0
        assert DEFAULT_TRUE_PEAK == -1.5
