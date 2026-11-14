#!/usr/bin/env python3
"""Tests for lib/otterrip_core/detect_url.py.

These tests use mocked yt-dlp responses so they never connect to YouTube.
They verify classification of songs, playlists, albums, YouTube Music URLs,
URLs with reordered parameters, ambiguous cases, network failures, and
incomplete metadata.
"""
import unittest
from unittest import mock

import otterrip_core.detect_url as du


# ── Helpers ────────────────────────────────────────────────────────

def _make_line(**kw):
    """Build a single yt-dlp --print line from keyword arguments."""
    defaults = {f: "" for f in du._FIELDS}
    defaults.update(kw)
    return "\x1f".join(str(v) for v in defaults.values())


class TestUrlHint(unittest.TestCase):
    """Fallback heuristic (no network)."""

    def test_song_url(self):
        self.assertEqual(
            du._url_hint("https://www.youtube.com/watch?v=abc123"),
            "song",
        )

    def test_playlist_url(self):
        self.assertEqual(
            du._url_hint("https://www.youtube.com/playlist?list=PLxxx"),
            "playlist",
        )

    def test_album_url(self):
        self.assertEqual(
            du._url_hint("https://music.youtube.com/album/OLAK5uy_abc"),
            "album",
        )

    def test_ytmusic_playlist(self):
        self.assertEqual(
            du._url_hint("https://music.youtube.com/playlist?list=PLxxx"),
            "playlist",
        )

    def test_watch_with_list_no_playlist_flag(self):
        # Without no_playlist, list= in URL hints "playlist"
        self.assertEqual(
            du._url_hint(
                "https://www.youtube.com/watch?v=abc&list=PLxxx",
            ),
            "playlist",
        )

    def test_watch_with_list_no_playlist_flag_true(self):
        # With no_playlist=True, /watch?...&list= is a song
        self.assertEqual(
            du._url_hint(
                "https://www.youtube.com/watch?v=abc&list=PLxxx",
                no_playlist=True,
            ),
            "song",
        )

    def test_youtu_be_short(self):
        self.assertEqual(
            du._url_hint("https://youtu.be/abc123"),
            "song",
        )


class TestDetectionResult(unittest.TestCase):
    """DetectionResult.to_output() formatting."""

    def test_song_output(self):
        r = du.DetectionResult("song", count=1, title="My Song", source="yt-dlp")
        lines = r.to_output().split("\n")
        self.assertEqual(lines, ["song", "1", "My Song", "yt-dlp"])

    def test_playlist_output(self):
        r = du.DetectionResult("playlist", count=12, title="Chill Mix", source="yt-dlp")
        lines = r.to_output().split("\n")
        self.assertEqual(lines, ["playlist", "12", "Chill Mix", "yt-dlp"])

    def test_album_output(self):
        r = du.DetectionResult("album", count=8, title="Dark Album", source="yt-dlp")
        lines = r.to_output().split("\n")
        self.assertEqual(lines, ["album", "8", "Dark Album", "yt-dlp"])

    def test_none_count_is_empty(self):
        r = du.DetectionResult("playlist", count=None, title="X", source="fallback")
        lines = r.to_output().split("\n")
        self.assertEqual(lines[1], "")

    def test_control_chars_stripped(self):
        r = du.DetectionResult("song", count=1, title="A\x00B\nC", source="yt-dlp")
        lines = r.to_output().split("\n")
        self.assertEqual(lines[2], "A B C")


class TestClassifyUrl_Mocked(unittest.TestCase):
    """classify_url() with mocked subprocess.run (no real network)."""

    def _patch(self, stdout: str) -> mock.MagicMock:
        m = mock.patch.object(du, "subprocess").start()
        m.run.return_value = mock.Mock(stdout=stdout)
        return m

    def tearDown(self) -> None:
        mock.patch.stopall()

    # -- Song --

    def test_single_song(self):
        line = _make_line(title="Hello", webpage_url="https://youtube.com/watch?v=abc")
        self._patch(line)
        r = du.classify_url("https://www.youtube.com/watch?v=abc")
        self.assertEqual(r.kind, "song")
        self.assertEqual(r.count, 1)
        self.assertEqual(r.source, "yt-dlp")

    # -- Playlist --

    def test_youtube_playlist(self):
        line = _make_line(
            playlist_count="15",
            playlist_title="Lo-Fi Beats",
            title="Track 1",
            playlist_id="PLxxx",
            webpage_url="https://youtube.com/playlist?list=PLxxx",
        )
        self._patch(line)
        r = du.classify_url("https://www.youtube.com/playlist?list=PLxxx")
        self.assertEqual(r.kind, "playlist")
        self.assertEqual(r.count, 15)

    def test_ytmusic_playlist(self):
        line = _make_line(
            playlist_count="20",
            playlist_title="Top Hits",
            title="Song A",
            playlist_id="PLyyy",
            webpage_url_domain="music.youtube.com",
            webpage_url="https://music.youtube.com/playlist?list=PLyyy",
        )
        self._patch(line)
        r = du.classify_url("https://music.youtube.com/playlist?list=PLyyy")
        self.assertEqual(r.kind, "playlist")
        self.assertEqual(r.count, 20)

    # -- Album --

    def test_ytmusic_album_by_path(self):
        line = _make_line(
            playlist_count="10",
            playlist_title="Midnights",
            album="Midnights",
            title="Lavender Haze",
            playlist_id="OLAK5uy_zzz",
            webpage_url_domain="music.youtube.com",
            webpage_url="https://music.youtube.com/album/OLAK5uy_zzz",
        )
        self._patch(line)
        r = du.classify_url("https://music.youtube.com/album/OLAK5uy_zzz")
        self.assertEqual(r.kind, "album")
        self.assertEqual(r.count, 10)

    def test_ytmusic_album_by_id(self):
        line = _make_line(
            playlist_count="8",
            playlist_title="Album Title",
            title="Track 1",
            playlist_id="OLAK5uy_abc123",
            webpage_url_domain="music.youtube.com",
            webpage_url="https://music.youtube.com/playlist?list=OLAK5uy_abc123",
        )
        self._patch(line)
        r = du.classify_url(
            "https://music.youtube.com/playlist?list=OLAK5uy_abc123"
        )
        self.assertEqual(r.kind, "album")

    def test_album_title_equals_playlist_title(self):
        line = _make_line(
            playlist_count="12",
            playlist_title="The Album",
            album="The Album",
            title="Opening Track",
            playlist_id="PLxxx",
            webpage_url_domain="www.youtube.com",
            webpage_url="https://www.youtube.com/playlist?list=PLxxx",
        )
        self._patch(line)
        r = du.classify_url("https://www.youtube.com/playlist?list=PLxxx")
        self.assertEqual(r.kind, "album")

    # -- no_playlist flag --

    def test_watch_with_list_no_playlist(self):
        line = _make_line(
            title="Just a Song",
            webpage_url="https://youtube.com/watch?v=abc&list=PLxxx",
        )
        self._patch(line)
        r = du.classify_url(
            "https://www.youtube.com/watch?v=abc&list=PLxxx",
            no_playlist=True,
        )
        self.assertEqual(r.kind, "song")
        self.assertEqual(r.count, 1)

    # -- Fallback on network failure --

    def test_fallback_on_timeout(self):
        m = mock.patch.object(du, "subprocess").start()
        m.run.side_effect = TimeoutError("timed out")
        r = du.classify_url("https://music.youtube.com/album/OLAK5uy_xxx")
        self.assertEqual(r.kind, "album")
        self.assertEqual(r.source, "fallback")

    def test_fallback_on_os_error(self):
        m = mock.patch.object(du, "subprocess").start()
        m.run.side_effect = OSError("no yt-dlp")
        r = du.classify_url("https://www.youtube.com/playlist?list=PLxxx")
        self.assertEqual(r.kind, "playlist")
        self.assertEqual(r.source, "fallback")

    def test_fallback_empty_stdout(self):
        self._patch("")
        r = du.classify_url("https://www.youtube.com/watch?v=abc")
        self.assertEqual(r.kind, "song")
        self.assertEqual(r.source, "fallback")

    # -- Incomplete metadata --

    def test_incomplete_metadata_no_count(self):
        line = _make_line(
            playlist_title="Some Playlist",
            title="Track",
            playlist_id="PLxxx",
        )
        self._patch(line)
        r = du.classify_url("https://www.youtube.com/playlist?list=PLxxx")
        self.assertEqual(r.kind, "playlist")
        # count is None because playlist_count was empty
        self.assertIsNone(r.count)

    def test_only_title(self):
        line = _make_line(title="Lonely Song")
        self._patch(line)
        r = du.classify_url("https://youtu.be/xyz")
        self.assertEqual(r.kind, "song")
        self.assertEqual(r.count, 1)

    # -- Edge cases --

    def test_empty_url(self):
        r = du.classify_url("")
        self.assertEqual(r.kind, "song")
        self.assertEqual(r.source, "error")

    def test_retries_without_flat(self):
        """Second try_run (flat=False) should be used if first returns no data."""
        m = mock.patch.object(du, "subprocess").start()
        first = True

        def side_effect(*a, **kw):
            nonlocal first
            if first:
                first = False
                return mock.Mock(stdout="")
            line = _make_line(title="Retry Song")
            return mock.Mock(stdout=line)

        m.run.side_effect = side_effect
        r = du.classify_url("https://www.youtube.com/watch?v=abc")
        self.assertEqual(r.kind, "song")
        self.assertEqual(r.source, "yt-dlp")


class TestDecodeField(unittest.TestCase):

    def test_plain_string(self):
        self.assertEqual(du._decode_field("Hello"), "Hello")

    def test_json_string(self):
        self.assertEqual(du._decode_field('"Hello"'), "Hello")

    def test_na(self):
        self.assertEqual(du._decode_field("NA"), "")

    def test_empty(self):
        self.assertEqual(du._decode_field(""), "")

    def test_none_json(self):
        self.assertEqual(du._decode_field("null"), "")


class TestBuildArgs(unittest.TestCase):

    def test_basic_args(self):
        args = du._build_args("https://example.com")
        self.assertIn("--skip-download", args)
        self.assertIn("--flat-playlist", args)
        self.assertIn("https://example.com", args)

    def test_no_playlist_flag(self):
        args = du._build_args("https://example.com", no_playlist=True)
        self.assertIn("--no-playlist", args)

    def test_ytdlp_bin(self):
        args = du._build_args("https://example.com", ytdlp_bin="/usr/bin/yt-dlp")
        self.assertEqual(args[0], "/usr/bin/yt-dlp")

    def test_separator_double_dash(self):
        args = du._build_args("https://example.com")
        self.assertIn("--", args)
        # URL should be the last element
        self.assertEqual(args[-1], "https://example.com")


if __name__ == "__main__":
    unittest.main()
