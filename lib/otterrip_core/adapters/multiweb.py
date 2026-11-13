#!/usr/bin/env python3
"""Multi-Web provider adapter (uses syncedlyrics library) — validated."""
from __future__ import annotations

import asyncio
import importlib.util
from typing import Optional

from otterrip_core.lyrics_bundle import LyricsBundle
from otterrip_core.lrc_validator import validate_and_normalise_lrc, normalise_lrc
from otterrip_core.query_clean import build_query_terms
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

    async def search(self, query: SongQuery) -> list[LyricsBundle]:
        if not self.is_available():
            return []
        try:
            from syncedlyrics import search as _search
        except ImportError:
            return []

        terms = build_query_terms(query.title, query.artist, query.album)
        search_term = terms["search"] or f"{query.title} {query.artist}".strip()

        bundles: list[LyricsBundle] = []
        for source in self.sources:
            try:
                text = await asyncio.to_thread(
                    _search,
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

            # Validate any LRC content found.
            validation = validate_and_normalise_lrc(
                text, audio_duration_s=query.duration
            )

            if validation.valid and validation.entries:
                bundles.append(LyricsBundle(
                    kind="line",
                    lrc=normalise_lrc(validation.entries),
                    plain=validation.plain,
                    lines=validation.entries,
                    source=f"multiweb | {source} | {search_term}",
                    audio_duration_ms=(
                        int(round(query.duration * 1000))
                        if query.duration is not None else None
                    ),
                    confidence=0.75,
                    warnings=validation.warnings,
                ))
                # Return best (first valid synced) — don't keep searching.
                break

            # If synced-only was requested and this source has no valid sync,
            # try plain as fallback (only if plain_ok).
            if not query.prefer_synced and text.strip():
                bundles.append(LyricsBundle(
                    kind="plain",
                    plain=text.strip(),
                    source=f"multiweb | {source} | {search_term}",
                    audio_duration_ms=(
                        int(round(query.duration * 1000))
                        if query.duration is not None else None
                    ),
                    confidence=0.5,
                ))
                break

        # Sort: synced > plain.
        bundles.sort(key=lambda b: (0 if b.has_sync else 1, -b.confidence))
        return bundles

    @classmethod
    def max_sync_level(cls):
        from otterrip_core.models import SyncLevel
        return SyncLevel.LINES
