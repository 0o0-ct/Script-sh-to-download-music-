#!/usr/bin/env python3
"""Basic tests for otterrip_core modules."""
import unittest
import asyncio
from contextlib import redirect_stdout
from io import StringIO
import sys
import tempfile
import json
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

from mutagen.id3 import ID3, SYLT, USLT
from mutagen.mp3 import MP3
from mutagen.flac import FLAC, VCommentDict
from mutagen.mp4 import MP4, MP4Tags

from otterrip_core.models import LyricsResult, SyncLevel
from otterrip_core.adapters.base import BaseProvider, SongQuery
from otterrip_core.adapters.lrclib import LrclibProvider, _parse_lrc
from otterrip_core.adapters.multiweb import MultiwebProvider
from otterrip_core.lyrics import (
    ITUNES_SYNCED_ATOM,
    _format_lrc,
    _collect_files,
    _embed_lyrics,
    _has_lyrics,
    _process_files_concurrently,
    _search_cascade,
    run_lyrics,
    select_best,
)


class TestModels(unittest.TestCase):
    """Test data models."""

    def test_sync_level_hierarchy(self):
        self.assertEqual(SyncLevel.NONE, 0)
        self.assertEqual(SyncLevel.LINES, 1)
        self.assertEqual(SyncLevel.WORDS, 2)
        self.assertEqual(SyncLevel.SYLLABLES, 3)

    def test_lyrics_result_plain(self):
        r = LyricsResult(provider="test", plain_text="hello world")
        self.assertTrue(r.is_plain)
        self.assertFalse(r.has_sync)

    def test_lyrics_result_synced(self):
        r = LyricsResult(
            provider="test",
            sync_level=SyncLevel.LINES,
            timed_entries=[(0.0, "line1"), (5.0, "line2")],
        )
        self.assertFalse(r.is_plain)
        self.assertTrue(r.has_sync)

    def test_score(self):
        r1 = LyricsResult(provider="a", sync_level=SyncLevel.LINES, confidence=0.8)
        r2 = LyricsResult(provider="b", sync_level=SyncLevel.WORDS, confidence=0.8)
        self.assertGreater(r2.score(), r1.score())


class TestLrclibAdapter(unittest.TestCase):
    """Test LRCLIB adapter utilities."""

    def test_parse_lrc(self):
        text = "[00:12.345] Hello world\n[01:02.678] Second line"
        entries = _parse_lrc(text)
        self.assertEqual(len(entries), 2)
        self.assertAlmostEqual(entries[0][0], 12.345, places=2)
        self.assertEqual(entries[0][1], "Hello world")
        self.assertAlmostEqual(entries[1][0], 62.678, places=2)

    def test_parse_lrc_empty(self):
        entries = _parse_lrc("no timestamps here")
        self.assertEqual(len(entries), 0)

    def test_query_clean_title(self):
        from otterrip_core.query_clean import clean_title
        self.assertEqual(clean_title("Song (Official Video)"), "Song")

    def test_query_clean_artist(self):
        from otterrip_core.query_clean import clean_artist
        self.assertEqual(clean_artist("Artist - Topic"), "Artist")

    def test_build_query_terms(self):
        from otterrip_core.query_clean import build_query_terms
        terms = build_query_terms("Song (Video)", "Artist - Topic", "Album")
        self.assertEqual(terms["title"], "Song")
        self.assertEqual(terms["artist"], "Artist")


class TestSelectBest(unittest.TestCase):
    """Test result selection logic."""

    def test_prefers_synced(self):
        results = [
            LyricsResult(provider="a", sync_level=SyncLevel.NONE, confidence=0.9, plain_text="text"),
            LyricsResult(provider="b", sync_level=SyncLevel.LINES, confidence=0.7, timed_entries=[(0, "x")]),
        ]
        best = select_best(results, SongQuery(title="t", artist="a"), prefer_synced=True)
        self.assertEqual(best.provider, "b")

    def test_prefers_higher_confidence(self):
        results = [
            LyricsResult(provider="a", sync_level=SyncLevel.LINES, confidence=0.5, timed_entries=[(0, "x")]),
            LyricsResult(provider="b", sync_level=SyncLevel.LINES, confidence=0.9, timed_entries=[(0, "y")]),
        ]
        best = select_best(results, SongQuery(title="t", artist="a"), prefer_synced=True)
        self.assertEqual(best.provider, "b")

    def test_rejects_low_confidence(self):
        results = [
            LyricsResult(provider="a", sync_level=SyncLevel.NONE, confidence=0.1, plain_text="wrong"),
        ]
        best = select_best(results, SongQuery(title="t", artist="a"), prefer_synced=False)
        self.assertIsNone(best)

    def test_rejects_empty_lyrics(self):
        result = LyricsResult(
            provider="a",
            sync_level=SyncLevel.LINES,
            confidence=0.9,
            timed_entries=[(0.0, " ")],
        )
        self.assertIsNone(select_best([result], SongQuery(title="t", artist="a")))

    def test_rejects_mismatched_title_and_artist(self):
        result = LyricsResult(
            provider="a",
            title="Different song",
            artist="Different artist",
            confidence=0.9,
            plain_text="Some lyrics",
        )
        self.assertIsNone(
            select_best(
                [result],
                SongQuery(title="Wanted song", artist="Wanted artist"),
                prefer_synced=False,
                plain_ok=True,
            )
        )

    def test_accepts_normalized_exact_match(self):
        result = LyricsResult(
            provider="a",
            title="Beyoncé - Déjà Vu!",
            artist="Beyoncé",
            confidence=0.9,
            plain_text="Some lyrics",
        )
        best = select_best(
            [result],
            SongQuery(title="Beyonce Deja Vu", artist="Beyonce"),
            prefer_synced=False,
            plain_ok=True,
        )
        self.assertIs(result, best)

    def test_no_results(self):
        best = select_best([], SongQuery(title="t", artist="a"))
        self.assertIsNone(best)

    def test_no_synced_when_preferred(self):
        results = [
            LyricsResult(provider="a", sync_level=SyncLevel.NONE, confidence=0.9, plain_text="text"),
        ]
        best = select_best(results, SongQuery(title="t", artist="a"), prefer_synced=True, plain_ok=False)
        self.assertIsNone(best)


class TestFormatLrc(unittest.TestCase):
    """Test LRC formatting."""

    def test_format_lrc(self):
        entries = [(0.0, "start"), (65.5, "one minute")]
        text = _format_lrc(entries)
        self.assertIn("[00:00.000] start", text)
        self.assertIn("[01:05.500] one minute", text)


class TestMultiwebAdapter(unittest.TestCase):
    def test_uses_only_verified_provider_names(self):
        self.assertEqual(MultiwebProvider.DEFAULT_SOURCES, (
            "musixmatch",
            "lrclib",
            "netease",
            "megalobiz",
            "genius",
        ))

    def test_falls_back_through_supported_syncedlyrics_sources(self):
        calls = []

        def fake_search(search_term, **kwargs):
            calls.append((search_term, kwargs))
            if kwargs["providers"] == ["musixmatch"]:
                return None
            return "[00:01.00] A lyric line"

        fake_module = types.ModuleType("syncedlyrics")
        fake_module.search = fake_search
        provider = MultiwebProvider()
        query = SongQuery(title="Song", artist="Artist", language="es")

        with patch.object(provider, "is_available", return_value=True):
            with patch.dict(sys.modules, {"syncedlyrics": fake_module}):
                results = asyncio.run(provider.search(query))

        self.assertEqual([call[1]["providers"] for call in calls], [
            ["musixmatch"],
            ["lrclib"],
        ])
        self.assertTrue(all(call[1]["synced_only"] for call in calls))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].timed_entries, [(1.0, "A lyric line")])

    def test_returns_plain_lyrics_when_sync_is_not_preferred(self):
        calls = []

        def fake_search(search_term, **kwargs):
            calls.append(kwargs)
            if kwargs["providers"] == ["genius"]:
                return "Plain lyric text"
            return None

        fake_module = types.ModuleType("syncedlyrics")
        fake_module.search = fake_search
        provider = MultiwebProvider()
        query = SongQuery(title="Song", artist="Artist", prefer_synced=False)

        with patch.object(provider, "is_available", return_value=True):
            with patch.dict(sys.modules, {"syncedlyrics": fake_module}):
                results = asyncio.run(provider.search(query))

        self.assertEqual(calls[-1]["providers"], ["genius"])
        self.assertTrue(all(call["plain_only"] for call in calls))
        self.assertFalse(any(call["synced_only"] for call in calls))
        self.assertEqual(results[0].plain_text, "Plain lyric text")
        self.assertFalse(results[0].has_sync)


class TestLyricsConcurrency(unittest.TestCase):
    def test_processes_files_within_worker_limit(self):
        async def run_test():
            active = 0
            maximum = 0

            async def process_file(path):
                nonlocal active, maximum
                active += 1
                maximum = max(maximum, active)
                await asyncio.sleep(0.01)
                active -= 1
                return path.name

            files = [Path(f"track-{index}.mp3") for index in range(8)]
            results = await _process_files_concurrently(files, process_file, jobs=3)
            return maximum, results

        maximum, results = asyncio.run(run_test())
        self.assertEqual(maximum, 3)
        self.assertEqual(len(results), 8)

    def test_run_lyrics_searches_and_embeds_concurrently(self):
        from otterrip_core.lyrics_bundle import LyricsBundle
        active = 0
        maximum = 0

        class FakeAudio:
            pass

        class FakeProvider(BaseProvider):
            name = "lrclib"

            async def search(self, query):
                nonlocal active, maximum
                active += 1
                maximum = max(maximum, active)
                await asyncio.sleep(0.01)
                active -= 1
                return [LyricsBundle(
                    kind="line",
                    lines=[(1000, "line")],
                    source=self.name,
                    title=query.title,
                    artist=query.artist,
                    confidence=0.9,
                )]

        files = [Path(f"track-{index}.mp3") for index in range(6)]
        args = SimpleNamespace(
            target="",
            fmt="mp3",
            host="https://lrclib.net",
            mode="embed",
            providers="lrclib",
            lang="",
            plain_ok=False,
            upgrade_plain=False,
            sync_level="line",
            sidecar=False,
            embed_style="lrc",
            min_confidence=0.0,
            offset_ms=0,
            cache_dir="/dev/shm",
            session="",
            summary="",
            overwrite=False,
            jobs=3,
        )
        fake_audio = FakeAudio()
        with patch("otterrip_core.lyrics._build_providers", return_value=[FakeProvider()]):
            with patch("otterrip_core.lyrics._collect_files", return_value=files):
                with patch("otterrip_core.lyrics._get_audio_file", return_value=fake_audio):
                    with patch(
                        "otterrip_core.lyrics._get_tags",
                        side_effect=lambda a: {"title": "Test", "artist": "Artist"},
                    ):
                        with patch("otterrip_core.lyrics._get_duration", return_value=100.0):
                            with patch("otterrip_core.lyrics._has_lyrics", return_value=False):
                                with patch("otterrip_core.lyrics._embed_lyrics", return_value=True) as embed:
                                    with patch("otterrip_core.lyrics.LyricsCache", return_value=None):
                                        with redirect_stdout(StringIO()):
                                            result = run_lyrics(args)

        self.assertEqual(result, 0)
        self.assertGreater(maximum, 0)
        self.assertLessEqual(maximum, 3)

    def test_session_files_are_deduplicated_before_concurrent_processing(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio_path = Path(tmp) / "track.mp3"
            audio_path.touch()
            session_path = Path(tmp) / "session.jsonl"
            session_path.write_text(
                f'{{"file": "{audio_path}"}}\n{{"file": "{audio_path}"}}\n',
                encoding="utf-8",
            )

            self.assertEqual(
                _collect_files(tmp, "mp3", str(session_path)),
                [audio_path],
            )


class TestLyricsEmbedding(unittest.TestCase):
    def test_mp3_embeds_real_timestamps_in_sylt_and_uslt(self):
        audio = MP3.__new__(MP3)
        audio.tags = ID3()
        lyrics = LyricsResult(
            provider="test",
            sync_level=SyncLevel.LINES,
            plain_text="First line\nSecond line",
            timed_entries=[(1.234, "First line"), (5.0, "Second line")],
        )

        with patch.object(MP3, "save"):
            self.assertTrue(_embed_lyrics(audio, lyrics))

        synced_frames = audio.tags.getall("SYLT")
        plain_frames = audio.tags.getall("USLT")
        self.assertEqual(len(synced_frames), 1)
        self.assertIsInstance(synced_frames[0], SYLT)
        self.assertEqual(synced_frames[0].format, 2)
        self.assertEqual(synced_frames[0].text, [
            ("First line", 1234),
            ("Second line", 5000),
        ])
        self.assertEqual(len(plain_frames), 1)
        self.assertIsInstance(plain_frames[0], USLT)
        self.assertEqual(plain_frames[0].text, "First line\nSecond line")

    def test_flac_keeps_synced_lyrics_in_syncedlyrics_tag(self):
        # Regresión: el FLAC solo conservaba el texto plano y perdía las
        # marcas de tiempo, así que la letra no se sincronizaba con el audio.
        # Ahora LYRICS también contiene el formato LRC con timestamps para
        # que los reproductores que solo leen LYRICS muestren la letra sincronizada.
        audio = FLAC.__new__(FLAC)
        audio.tags = VCommentDict()
        lyrics = LyricsResult(
            provider="test",
            sync_level=SyncLevel.LINES,
            plain_text="First line\nSecond line",
            timed_entries=[(1.234, "First line"), (5.0, "Second line")],
        )

        with patch.object(FLAC, "save"):
            self.assertTrue(_embed_lyrics(audio, lyrics))

        # LYRICS ahora contiene LRC con timestamps cuando hay sync
        lyr_lines = audio["LYRICS"]
        self.assertIn("[00:01.234] First line", lyr_lines)
        self.assertIn("[00:05.000] Second line", lyr_lines)
        # SYNCEDLYRICS también debe contener los timestamps
        synced = audio["SYNCEDLYRICS"][0]
        self.assertIn("[00:01.234]", synced)
        self.assertIn("[00:05.000]", synced)

    def test_m4a_keeps_synced_lyrics_in_itunes_atom(self):
        # Regresión: el M4A solo conservaba el texto plano (©lyr) y perdía las
        # marcas de tiempo, así que la letra no se sincronizaba con el audio.
        # Ahora ©lyr también contiene el formato LRC con timestamps para que
        # los reproductores que solo leen ©lyr muestren la letra sincronizada.
        audio = MP4.__new__(MP4)
        audio.tags = MP4Tags()
        lyrics = LyricsResult(
            provider="test",
            sync_level=SyncLevel.LINES,
            plain_text="First line\nSecond line",
            timed_entries=[(1.234, "First line"), (5.0, "Second line")],
        )

        with patch.object(MP4, "save"):
            self.assertTrue(_embed_lyrics(audio, lyrics))

        # ©lyr ahora contiene LRC con timestamps cuando hay sync
        lyr_content = audio.tags["\xa9lyr"][0]
        self.assertIn("[00:01.234] First line", lyr_content)
        self.assertIn("[00:05.000] Second line", lyr_content)
        # El átomo iTunes también debe contener los timestamps
        atom = audio.tags[ITUNES_SYNCED_ATOM][0]
        if isinstance(atom, bytes):
            atom = atom.decode("utf-8")
        self.assertIn("[00:01.234]", atom)
        self.assertIn("[00:05.000]", atom)

    def test_flac_plain_lyrics_no_timestamps(self):
        # Cuando no hay sync, LYRICS debe contener texto plano sin timestamps.
        audio = FLAC.__new__(FLAC)
        audio.tags = VCommentDict()
        lyrics = LyricsResult(
            provider="test",
            sync_level=SyncLevel.NONE,
            plain_text="First line\nSecond line",
        )

        with patch.object(FLAC, "save"):
            self.assertTrue(_embed_lyrics(audio, lyrics))

        self.assertEqual(audio["LYRICS"], ["First line", "Second line"])
        self.assertNotIn("SYNCEDLYRICS", audio)

    def test_m4a_plain_lyrics_no_timestamps(self):
        # Cuando no hay sync, ©lyr debe contener texto plano sin timestamps.
        audio = MP4.__new__(MP4)
        audio.tags = MP4Tags()
        lyrics = LyricsResult(
            provider="test",
            sync_level=SyncLevel.NONE,
            plain_text="First line\nSecond line",
        )

        with patch.object(MP4, "save"):
            self.assertTrue(_embed_lyrics(audio, lyrics))

        self.assertEqual(audio.tags["\xa9lyr"], ["First line\nSecond line"])
        self.assertNotIn(ITUNES_SYNCED_ATOM, audio.tags)

    def test_has_lyrics_detects_synced_only_tags(self):
        flac = FLAC.__new__(FLAC)
        flac.tags = VCommentDict()
        flac["SYNCEDLYRICS"] = ["[00:01.000]Only synced"]
        self.assertTrue(_has_lyrics(flac))

        mp4 = MP4.__new__(MP4)
        mp4.tags = MP4Tags()
        mp4.tags[ITUNES_SYNCED_ATOM] = [b"[00:01.000]Only synced"]
        self.assertTrue(_has_lyrics(mp4))

    def test_cascade_uses_next_provider_after_invalid_result(self):
        from otterrip_core.lyrics_bundle import LyricsBundle
        query = SongQuery(title="Song", artist="Artist")

        class FakeProvider(BaseProvider):
            def __init__(self, name, results):
                self.name = name
                self.results = results
                self.calls = 0

            async def search(self, _query):
                self.calls += 1
                return self.results

        invalid = LyricsBundle(kind="plain", plain="", confidence=0.9)
        valid = LyricsBundle(
            kind="line",
            lines=[(1000, "line")],
            source="second",
            confidence=0.9,
            title="Song",
            artist="Artist",
        )
        first = FakeProvider("first", [invalid])
        second = FakeProvider("second", [valid])

        best = asyncio.run(
            _search_cascade(
                [first, second],
                query,
                prefer_synced=True,
                plain_ok=False,
            )
        )

        self.assertIsNotNone(best)
        self.assertEqual(best.kind, "line")
        self.assertEqual(first.calls, 1)
        self.assertEqual(second.calls, 1)


class TestMergeSessions(unittest.TestCase):
    """Test session merging."""

    def test_merge_deduplicates(self):
        from otterrip_core.merge_sessions import run_merge_sessions

        with tempfile.TemporaryDirectory() as tmp:
            f1 = Path(tmp) / "s1"
            f2 = Path(tmp) / "s2"
            out = Path(tmp) / "out"
            f1.write_text("id1\nid2\n")
            f2.write_text("id2\nid3\n")

            args = MagicMock()
            args.output = str(out)
            args.inputs = [str(f1), str(f2)]
            run_merge_sessions(args)

            lines = out.read_text().strip().split("\n")
            self.assertEqual(len(lines), 3)
            self.assertIn("id1", lines)
            self.assertIn("id2", lines)
            self.assertIn("id3", lines)


class TestClassifyLog(unittest.TestCase):
    """Test log classification."""

    def test_classify_permanent(self):
        from otterrip_core.classify_log import classify_line
        self.assertEqual(classify_line("ERROR: 404 Not Found"), "permanent")

    def test_classify_service_unavailable(self):
        from otterrip_core.classify_log import classify_line
        self.assertEqual(classify_line("ERROR: 503 Service Unavailable"), "service_unavailable")

    def test_classify_recoverable(self):
        from otterrip_core.classify_log import classify_line
        self.assertEqual(classify_line("ERROR: Read timed out"), "recoverable")


if __name__ == "__main__":
    unittest.main()
