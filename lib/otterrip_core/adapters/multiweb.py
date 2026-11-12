#!/usr/bin/env python3
"""Multi-Web provider adapter (uses syncedlyrics library)."""
from __future__ import annotations

import asyncio
import importlib.util

from otterrip_core.models import LyricsResult, SyncLevel
from otterrip_core.adapters.base import BaseProvider, SongQuery


class MultiwebProvider(BaseProvider):
    """Multi-Web adapter via syncedlyrics library."""

    name = "multiweb"
    enabled_by_default = True
    requires_credentials = False
    DEFAULT_SOURCES = ("musixmatch", "lrclib", "netease", "megalobiz", "genius")

    def __init__(self, sources: tuple[str, ...] | None = None):
        self.sources = self.DEFAULT_SOURCES if sources is None else sources

    def is_available(self) -> bool:
        return importlib.util.find_spec("syncedlyrics") is not None

    async def search(self, query: SongQuery) -> list[LyricsResult]:
        if not self.is_available():
            return []
        try:
            from syncedlyrics import search
        except ImportError:
            return []

        search_term = f"{query.title} {query.artist}".strip()
        for source in self.sources:
            try:
                text = await asyncio.to_thread(
                    search,
                    search_term,
                    providers=[source],
                    synced_only=query.prefer_synced,
                    plain_only=not query.prefer_synced,
                    lang=query.language if source == "musixmatch" else None,
                )
            except Exception:
                continue
            if not isinstance(text, str) or not text.strip():
                continue

            entries = self._parse_lrc(text)
            if entries and any(line.strip() for _, line in entries):
                result = LyricsResult(
                    provider=self.name,
                    title=query.title,
                    artist=query.artist,
                    album=query.album,
                    duration=query.duration,
                    language=query.language,
                    sync_level=SyncLevel.LINES,
                    plain_text="\n".join(line for _, line in entries),
                    timed_entries=entries,
                    original_format="lrc",
                    confidence=0.7,
                )
                return [result]
            if query.prefer_synced or not text.strip():
                continue

            return [LyricsResult(
                provider=self.name,
                title=query.title,
                artist=query.artist,
                album=query.album,
                duration=query.duration,
                language=query.language,
                plain_text=text.strip(),
                original_format="txt",
                confidence=0.7,
            )]

        return []

    @classmethod
    def max_sync_level(cls) -> SyncLevel:
        return SyncLevel.LINES

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
