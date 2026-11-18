#!/usr/bin/env python3
"""Regression tests for the LRCLIB /api/search fallback.

The adapter used to rely solely on ``/api/get`` (exact match), so tracks
whose tag title carries YouTube junk ("Canción - Artista - Playlist") or
case/accent drift ("DESCALZOS" vs "Descalzos") were never found even when
LRCLIB had the lyrics. These tests pin the tolerant-search fallbacks.
"""
import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))

from otterrip_core.adapters.base import SongQuery
from otterrip_core.adapters.lrclib import LrclibProvider
from otterrip_core.query_clean import build_query_terms, strip_artist_from_title


def _row(track: str, artist: str, synced: bool = True) -> dict:
    lrc = "\n".join(
        f"[{i * 10 // 60:02d}:{i * 10 % 60:02d}.00]línea {i + 1}" for i in range(10)
    )
    plain = "\n".join(f"línea {i + 1}" for i in range(10))
    return {
        "id": hash((track, artist)) % 100000,
        "trackName": track,
        "artistName": artist,
        "albumName": "Album",
        "duration": 100.0,
        "instrumental": False,
        "plainLyrics": plain,
        "syncedLyrics": lrc if synced else None,
    }


ROWS_BY_SEARCH = {}  # filled per-test via make_server


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - http.server API
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/api/get":
            # The whole point: exact match never succeeds in these scenarios.
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if u.path == "/api/search":
            if "q" in q:
                rows = _Handler.Q_ROWS
            else:
                key = (q.get("track_name", [""])[0], q.get("artist_name", [""])[0])
                rows = _Handler.FILTERED_ROWS.get(key, [])
                # title-only query: artist_name absent
                if not rows and "artist_name" not in q:
                    rows = _Handler.TITLE_ONLY_ROWS.get(
                        q.get("track_name", [""])[0], []
                    )
            body = json.dumps(rows).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


def make_server(filtered, q_rows, title_only=None):
    """Start a fake LRCLIB on an ephemeral port; returns (provider, server)."""
    _Handler.FILTERED_ROWS = filtered
    _Handler.Q_ROWS = q_rows
    _Handler.TITLE_ONLY_ROWS = title_only or {}
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    provider = LrclibProvider(host=f"http://127.0.0.1:{srv.server_address[1]}")
    return provider, srv


class TestApiSearchFallback(unittest.TestCase):
    def _search(self, provider, **kwargs):
        import asyncio

        return asyncio.run(provider.search(SongQuery(**kwargs)))

    def test_case_and_junk_found_via_filtered_search(self):
        # Tag "DESCALZOS (Letra) | Hakuna | Música Católica" cleans to
        # "DESCALZOS"; LRCLIB has it as "Descalzos" — /api/get misses on case.
        rows = [_row("Descalzos", "Hakuna Group Music")]
        provider, srv = make_server(
            filtered={("DESCALZOS", "Hakuna Group Music"): rows},
            q_rows=[],
        )
        try:
            bundles = self._search(
                provider, title="DESCALZOS", artist="Hakuna Group Music"
            )
        finally:
            srv.shutdown()
        self.assertTrue(bundles)
        self.assertTrue(bundles[0].has_sync)
        self.assertIn("línea 1", bundles[0].lrc)

    def test_compound_title_found_via_q_full_text(self):
        # Filtered search misses the compound "Canción - Artista - Playlist"
        # title; the full-text q= endpoint tolerates it.
        rows = [_row("Cómo Te Explico", "Aldana Canale")]
        provider, srv = make_server(
            filtered={}, q_rows=rows,
        )
        try:
            bundles = self._search(
                provider,
                title="Cómo Te Explico - Aldana Canale",
                artist="Aldana Canale",
            )
        finally:
            srv.shutdown()
        self.assertTrue(bundles)
        self.assertTrue(bundles[0].has_sync)

    def test_title_only_fallback_when_artist_differs(self):
        rows = [_row("SALMO 23", "Un Corazón")]
        provider, srv = make_server(
            filtered={},
            q_rows=[],
            title_only={"SALMO 23": rows},
        )
        try:
            bundles = self._search(
                provider,
                title="SALMO 23",
                artist="Un Corazón, Kim Richards, Marco Barrientos",
            )
        finally:
            srv.shutdown()
        self.assertTrue(bundles)
        self.assertTrue(bundles[0].has_sync)

    def test_no_results_anywhere(self):
        provider, srv = make_server(filtered={}, q_rows=[], title_only={})
        try:
            bundles = self._search(
                provider, title="Hágase", artist="Aldana Canale"
            )
        finally:
            srv.shutdown()
        self.assertEqual(bundles, [])

    def test_row_score_prefers_exact_match_and_sync(self):
        score = LrclibProvider._row_score
        hi = _row("El abrazo", "Hakuna Group Music", synced=True)
        lo = _row("El abrazo (Cover)", "Otro Artista", synced=True)
        self.assertGreater(
            score(hi, "hakuna group music", "el abrazo", 100.0),
            score(lo, "hakuna group music", "el abrazo", 100.0),
        )


    def test_garbage_album_dropped(self):
        # "NA" poisoned /api/get: the track existed but album_name=NA → 404.
        terms = build_query_terms("Descalzos", "Hakuna Group Music", "NA")
        self.assertEqual(terms["album"], "")
        terms = build_query_terms("X", "Y", "Unknown Album")
        self.assertEqual(terms["album"], "")
        terms = build_query_terms("X", "Y", "Somos Iglesia")
        self.assertEqual(terms["album"], "Somos Iglesia")


class TestStripArtistFromTitle(unittest.TestCase):
    """The real-world patterns from the Aldana Canale playlist run."""

    def test_suffix_only(self):
        self.assertEqual(
            strip_artist_from_title("24/7 - Aldana Canale", "Aldana Canale"),
            "24/7",
        )

    def test_suffix_with_playlist_context(self):
        self.assertEqual(
            strip_artist_from_title(
                "Un Paso A La Vez - Aldana Canale - Oración para momentos difíciles",
                "Aldana Canale",
            ),
            "Un Paso A La Vez",
        )

    def test_prefix(self):
        self.assertEqual(
            strip_artist_from_title("Aldana Canale - Hágase", "Aldana Canale"),
            "Hágase",
        )

    def test_accent_insensitive(self):
        self.assertEqual(
            strip_artist_from_title("Pablo Martínez - Tarde te amé", "Pablo Martinez"),
            "Tarde te amé",
        )

    def test_no_artist_no_change(self):
        self.assertEqual(strip_artist_from_title("Canción - Otro", ""), "Canción - Otro")

    def test_unrelated_segments_kept(self):
        self.assertEqual(
            strip_artist_from_title("Stairway To Heaven - Led Zeppelin", "Deep Purple"),
            "Stairway To Heaven - Led Zeppelin",
        )

    def test_build_query_terms_end_to_end(self):
        terms = build_query_terms(
            "Resucitó - Aldana Canale - Música Católica para PASCUA",
            "Aldana Canale",
        )
        self.assertEqual(terms["title"], "Resucitó")
        self.assertEqual(terms["artist"], "Aldana Canale")

    def test_build_query_terms_accent_prefix(self):
        terms = build_query_terms("Pablo Martínez - Tarde te amé", "Pablo Martinez")
        self.assertEqual(terms["title"], "Tarde te amé")


if __name__ == "__main__":
    unittest.main()
