#!/usr/bin/env python3
"""Tests for otterrip_core.clean_names (filename cleaning)."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from otterrip_core.clean_names import (
    container_ext,
    clean_and_rename,
    plan_name,
    split_ext,
)


class TestSplitExt(unittest.TestCase):
    def test_plain_extension(self):
        self.assertEqual(split_ext("Song [Official].mp3"), ("Song [Official]", ".mp3"))

    def test_compound_subtitles(self):
        self.assertEqual(split_ext("Song [Official].en.lrc"), ("Song [Official]", ".en.lrc"))
        self.assertEqual(split_ext("Song [Official].es.vtt"), ("Song [Official]", ".es.vtt"))
        self.assertEqual(split_ext("Song [Official].es-419.lrc"), ("Song [Official]", ".es-419.lrc"))

    def test_double_digit_lang_without_container_match(self):
        # ".en.m4a" is not a compound subtitle form: plain splitext applies.
        self.assertEqual(split_ext("Song.en.m4a"), ("Song.en", ".m4a"))

    def test_no_extension(self):
        self.assertEqual(split_ext("README"), ("README", ""))


class TestContainerExt(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(container_ext(".mp3"), ".mp3")

    def test_compound(self):
        self.assertEqual(container_ext(".en.lrc"), ".lrc")
        self.assertEqual(container_ext(".es.vtt"), ".vtt")

    def test_empty(self):
        self.assertEqual(container_ext(""), "")


class TestPlanName(unittest.TestCase):
    def test_user_example(self):
        base = (
            "Goo Goo Dolls – Iris [Official Music Video] "
            "[4K Remaster] [NdYWuo9OFAw]"
        )
        self.assertEqual(plan_name(base), "Goo Goo Dolls - Iris")

    def test_keeps_single_artist_song_form(self):
        self.assertEqual(plan_name("Goo Goo Dolls - Iris"), "Goo Goo Dolls - Iris")

    def test_removes_repeated_artist_prefix(self):
        self.assertEqual(
            plan_name("JON CARLO - Jon Carlo - Quema Mi Vida"),
            "Quema Mi Vida",
        )

    def test_removes_common_noise(self):
        self.assertEqual(plan_name("Song (Lyric Video) [HD]"), "Song")
        self.assertEqual(plan_name("Song (En Vivo desde Bogotá)"), "Song")
        self.assertEqual(plan_name("Song - Musica Catolica"), "Song")

    def test_already_clean_unchanged(self):
        self.assertEqual(plan_name("Artista - Canción"), "Artista - Canción")

    def test_empty(self):
        self.assertEqual(plan_name(""), "")


class TestCleanAndRename(unittest.TestCase):
    def _write(self, path: Path, content: str = "x") -> None:
        path.write_text(content, encoding="utf-8")

    def test_renames_files_and_manifests(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "Music"
            (dest / "Mi Playlist").mkdir(parents=True)
            self._write(dest / "Goo Goo Dolls – Iris [Official Music Video] [4K Remaster] [NdYWuo9OFAw].m4a")
            self._write(dest / "Goo Goo Dolls – Iris [Official Music Video] [4K Remaster] [NdYWuo9OFAw].en.lrc")
            self._write(dest / "Goo Goo Dolls – Iris [Official Music Video] [4K Remaster] [NdYWuo9OFAw].jpg")
            self._write(dest / "Mi Playlist" / "Song (Official Audio) [xYz_-123456].mp3")
            # No renombrar: extensión no soportada / nombre ya limpio.
            self._write(dest / "Song [Official].part")
            self._write(dest / "Artista - Canción.m4a")

            session = Path(tmp) / "session.final"
            verified = Path(tmp) / "session.verified"
            session.write_text(
                json.dumps({
                    "filepath": str(dest / "Goo Goo Dolls – Iris [Official Music Video] [4K Remaster] [NdYWuo9OFAw].m4a"),
                    "title": "Goo Goo Dolls – Iris (Official Music Video)",
                    "id": "NdYWuo9OFAw",
                }, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            verified.write_text(
                json.dumps({
                    "file": str(dest / "Mi Playlist" / "Song (Official Audio) [xYz_-123456].mp3"),
                    "duration": 180.0,
                    "cover_missing": False,
                }, ensure_ascii=False) + "\n"
                + "NdYWuo9OFAw\n",  # línea cruda (id): debe conservarse tal cual
                encoding="utf-8",
            )

            renamed = clean_and_rename(
                str(dest), [str(session), str(verified)]
            )
            self.assertEqual(renamed, 4)

            names = sorted(p.name for p in dest.rglob("*") if p.is_file())
            self.assertIn("Goo Goo Dolls - Iris.m4a", names)
            self.assertIn("Goo Goo Dolls - Iris.en.lrc", names)
            self.assertIn("Goo Goo Dolls - Iris.jpg", names)
            self.assertIn("Song.mp3", names)
            self.assertIn("Song [Official].part", names)
            self.assertIn("Artista - Canción.m4a", names)
            self.assertNotIn(
                "Goo Goo Dolls – Iris [Official Music Video] [4K Remaster] [NdYWuo9OFAw].m4a",
                names,
            )

            session_info = json.loads(session.read_text(encoding="utf-8").splitlines()[0])
            self.assertEqual(
                session_info["filepath"],
                str(dest / "Goo Goo Dolls - Iris.m4a"),
            )
            verified_lines = verified.read_text(encoding="utf-8").splitlines()
            self.assertEqual(
                json.loads(verified_lines[0])["file"],
                str(dest / "Mi Playlist" / "Song.mp3"),
            )
            # La línea cruda se preserva sin cambios.
            self.assertEqual(verified_lines[1], "NdYWuo9OFAw")

    def test_collision_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            self._write(dest / "Song [Official Video].m4a", "junk")
            self._write(dest / "Song.m4a", "real")
            renamed = clean_and_rename(str(dest))
            self.assertEqual(renamed, 0)
            self.assertTrue((dest / "Song [Official Video].m4a").exists())
            self.assertEqual((dest / "Song.m4a").read_text(encoding="utf-8"), "real")

    def test_missing_dest_returns_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                clean_and_rename(str(Path(tmp) / "no-existe")), 0
            )

    def test_dry_run_touches_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            self._write(dest / "Song [Official Video].m4a")
            renamed = clean_and_rename(str(dest), dry_run=True)
            self.assertEqual(renamed, 1)
            self.assertTrue((dest / "Song [Official Video].m4a").exists())


if __name__ == "__main__":
    unittest.main()
