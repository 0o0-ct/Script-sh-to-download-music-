#!/usr/bin/env python3
"""Multi-Web provider adapter (uses syncedlyrics library)."""
from __future__ import annotations

import importlib.util
from typing import Optional

from otterrip_core.models import LyricsResult, SyncLevel
from otterrip_core.adapters.base import BaseProvider, SongQuery


class MultiwebProvider(BaseProvider):
    """Multi-Web adapter via syncedlyrics library."""

    name = "multiweb"
    enabled_by_default = True
    requires_credentials = False

    def is_available(self) -> bool:
        return importlib.util.find_spec("syncedlyrics") is not None

    async def search(self, query: SongQuery) -> list[LyricsResult]:
        results: list[LyricsResult] = []
        if not self.is_available():
            return results

        try:
            from syncedlyrics import (
                get_genius_lyrics,
                get_musixmatch_lyrics,
                get_synced_lyrics,
                get_lyrics_fallback,
                format_lyrics_lrc,
            )

            # Try synced lyrics first (word-level)
            synced_text = ""
            try:
                synced_text = get_synced_lyrics(
                    query.title,
                    query.artist,
                    search_ytm=True,
                    search_klyrics=True,
                    search_synced=True,
                    search_musixmatch=True,
                )
            except TypeError:
                # Older version with fewer params
                try:
                    synced_text = get_synced_lyrics(query.title, query.artist)
                except Exception:
                    synced_text = ""

            if synced_text:
                # Detect sync level
                entries = self._parse_lrc(synced_text)
                sync_level = SyncLevel.NONE
                if entries:
                    # Heuristic: if many short entries, likely word-level
                    avg_len = sum(len(t) for _, t in entries) / len(entries)
                    if avg_len < 5:
                        sync_level = SyncLevel.WORDS
                    elif avg_len < 15:
                        sync_level = SyncLevel.SYLLABLES
                    else:
                        sync_level = SyncLevel.LINES

                results.append(LyricsResult(
                    provider=self.name,
                    title=query.title,
                    artist=query.artist,
                    album=query.album,
                    duration=query.duration,
                    language=query.language,
                    sync_level=sync_level,
                    plain_text="\n".join(t for _, t in entries) if entries else synced_text,
                    timed_entries=entries,
                    original_format="lrc",
                    confidence=0.7,
                ))

            # Try plain lyrics as fallback
            if not results:
                plain_text = ""
                try:
                    plain_text = get_lyrics_fallback(query.title, query.artist)
                except Exception:
                    plain_text = ""

                if plain_text:
                    results.append(LyricsResult(
                        provider=self.name,
                        title=query.title,
                        artist=query.artist,
                        album=query.album,
                        duration=query.duration,
                        language=query.language,
                        sync_level=SyncLevel.NONE,
                        plain_text=plain_text,
                        original_format="txt",
                        confidence=0.5,
                    ))

        except ImportError:
            pass
        except Exception:
            pass

        return results

    @classmethod
    def max_sync_level(cls) -> SyncLevel:
        return SyncLevel.WORDS

    @staticmethod
    def _parse_lrc(text: str) -> list[tuple[float, str]]:
        """Parse LRC format."""
        import re
        entries = []
        pat = re.compile(r"\[(\d{2}):(\d{2})(?:\.(\d{2,3}))?\]\s*(.*)")
        for line in text.splitlines():
            m = pat.match(line.strip())
            if m:
                mins, secs = int(m.group(1)), int(m.group(2))
                ms = m.group(3) or "0"
                if len(ms) == 2:
                    ms += "0"
                time_s = mins * 60 + secs + int(ms) / 1000.0
                entries.append((time_s, m.group(4)))
        return entries
