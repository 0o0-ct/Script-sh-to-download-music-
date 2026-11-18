#!/usr/bin/env python3
"""Multi-Web provider adapter (uses syncedlyrics library) — validated.

Adds exponential backoff retries for transient failures so that a temporary
network glitch on one source does not skip the rest of the chain.
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
from typing import Optional

from otterrip_core.lyrics_bundle import LyricsBundle
from otterrip_core.lrc_validator import validate_and_normalise_lrc, normalise_lrc
from otterrip_core.query_clean import build_query_terms
from otterrip_core.adapters.base import BaseProvider, SongQuery

logger = logging.getLogger("otterrip_core.multiweb")


class MultiwebProvider(BaseProvider):
    """Multi-Web adapter via syncedlyrics library."""

    name = "multiweb"
    enabled_by_default = True
    requires_credentials = False
    DEFAULT_SOURCES = ("musixmatch", "lrclib", "netease", "megalobiz", "genius")

    # Exponential backoff config
    MAX_RETRIES = 3
    BACKOFF_BASE = 1.0  # seconds

    def __init__(self, sources: tuple[str, ...] | None = None):
        self.sources = self.DEFAULT_SOURCES if sources is None else sources

    def is_available(self) -> bool:
        return importlib.util.find_spec("syncedlyrics") is not None

    async def _search_with_backoff(
        self,
        search_func,
        search_term: str,
        source: str,
        prefer_synced: bool,
        lang: Optional[str],
    ) -> Optional[str]:
        """Call search_func with exponential backoff on transient errors."""
        for attempt in range(self.MAX_RETRIES):
            try:
                text = await asyncio.to_thread(
                    search_func,
                    search_term,
                    providers=[source],
                    synced_only=prefer_synced,
                    plain_only=not prefer_synced,
                    lang=lang,
                )
                return text
            except Exception as exc:
                if attempt < self.MAX_RETRIES - 1:
                    wait = self.BACKOFF_BASE * (2 ** attempt)
                    logger.debug(
                        "multiweb %s attempt %d failed (%s), retrying in %.1fs",
                        source, attempt + 1, exc, wait,
                    )
                    await asyncio.sleep(wait)
                else:
                    logger.warning(
                        "multiweb %s failed after %d attempts: %s",
                        source, self.MAX_RETRIES, exc,
                    )
        return None

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
            text = await self._search_with_backoff(
                _search,
                search_term,
                source,
                query.prefer_synced,
                query.language if source == "musixmatch" else None,
            )
            if text is None:
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
