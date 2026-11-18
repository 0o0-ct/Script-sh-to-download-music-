#!/usr/bin/env python3
"""Individual syncedlyrics provider adapters.

Each source supported by the syncedlyrics library (Musixmatch, NetEase,
Megalobiz, Genius) is exposed as a standalone :class:`BaseProvider` so
that the cascade in ``lyrics.py`` can query them independently, collect
results from all, and pick the best one with fuzzy scoring.

Order of quality (sync-first):
    1. Lrclib          — best sync quality, free
    2. Musixmatch      — large catalog, good sync
    3. NetEase         — good for C-pop / Asian music
    4. Megalobiz       — fallback sync
    5. Genius          — plain text only, no sync

All providers share the same backoff helper and LRC validation logic.
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import time
from typing import Optional

from otterrip_core.lyrics_bundle import LyricsBundle
from otterrip_core.lrc_validator import validate_and_normalise_lrc, normalise_lrc
from otterrip_core.query_clean import build_query_terms
from otterrip_core.adapters.base import BaseProvider, SongQuery

logger = logging.getLogger("otterrip_core.syncedlyrics_adapter")


# ── Exponential backoff helper ─────────────────────────────────────

async def _search_with_backoff(
    search_func,
    search_term: str,
    source: str,
    prefer_synced: bool,
    lang: Optional[str],
    max_retries: int = 3,
    backoff_base: float = 1.0,
) -> Optional[str]:
    """Call *search_func* with exponential backoff on transient errors.

    Returns the lyrics text (synced or plain) or ``None`` on failure.
    """
    for attempt in range(max_retries):
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
            if attempt < max_retries - 1:
                wait = backoff_base * (2 ** attempt)
                logger.debug(
                    "syncedlyrics %s attempt %d failed (%s), retrying in %.1fs",
                    source, attempt + 1, exc, wait,
                )
                await asyncio.sleep(wait)
            else:
                logger.warning(
                    "syncedlyrics %s failed after %d attempts: %s",
                    source, max_retries, exc,
                )
    return None


# ── Source metadata ────────────────────────────────────────────────

# (source_name, supports_sync, supports_lang)
SOURCE_META = {
    "musixmatch":  (True,  True),   # best non-lrclib sync, lang filter
    "netease":     (True,  False),  # good for C-pop
    "megalobiz":   (True,  False),  # fallback sync
    "genius":      (False, False),  # plain text only
}


# ── Individual provider class ──────────────────────────────────────

class SyncedlyricsProvider(BaseProvider):
    """A single syncedlyrics source as a standalone provider.

    Each instance wraps one source (e.g. "musixmatch") and searches only
    that source.  This allows the cascade to query all sources and pick
    the best result across providers.

    Args:
        source: One of "musixmatch", "netease", "megalobiz", "genius".
    """

    enabled_by_default = True
    requires_credentials = False
    MAX_RETRIES = 3
    BACKOFF_BASE = 1.0

    def __init__(self, source: str):
        if source not in SOURCE_META:
            raise ValueError(f"Unknown syncedlyrics source: {source!r}")
        self.source = source
        self._supports_sync, self._supports_lang = SOURCE_META[source]

    @property
    def name(self) -> str:
        return self.source

    def is_available(self) -> bool:
        return importlib.util.find_spec("syncedlyrics") is not None

    @classmethod
    def max_sync_level(cls):
        from otterrip_core.models import SyncLevel
        return SyncLevel.LINES

    async def search(self, query: SongQuery) -> list[LyricsBundle]:
        if not self.is_available():
            return []
        try:
            from syncedlyrics import search as _search
        except ImportError:
            return []

        terms = build_query_terms(query.title, query.artist, query.album)
        search_term = terms["search"] or f"{query.title} {query.artist}".strip()

        prefer_synced = query.prefer_synced and self._supports_sync
        lang = query.language if self._supports_lang else None

        t0 = time.monotonic()
        text = await _search_with_backoff(
            _search, search_term, self.source, prefer_synced, lang,
            max_retries=self.MAX_RETRIES, backoff_base=self.BACKOFF_BASE,
        )
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        if text is None:
            logger.debug("%s: no result (%d ms)", self.source, elapsed_ms)
            return []

        if not isinstance(text, str) or not text.strip():
            logger.debug("%s: empty result (%d ms)", self.source, elapsed_ms)
            return []

        bundles: list[LyricsBundle] = []

        # Validate any LRC content found.
        validation = validate_and_normalise_lrc(
            text, audio_duration_s=query.duration
        )

        if validation.valid and validation.entries:
            confidence = self._confidence(query, validation)
            bundles.append(LyricsBundle(
                kind="line",
                lrc=normalise_lrc(validation.entries),
                plain=validation.plain,
                lines=validation.entries,
                source=f"{self.source} | {search_term}",
                audio_duration_ms=(
                    int(round(query.duration * 1000))
                    if query.duration is not None else None
                ),
                confidence=confidence,
                warnings=validation.warnings + [f"{elapsed_ms} ms"],
            ))
            logger.info(
                "%s: synced LRC (%d lines, %d ms, conf=%.2f)",
                self.source, len(validation.entries), elapsed_ms, confidence,
            )
            return bundles

        # If synced-only was requested and this source has no valid sync,
        # try plain as fallback.
        if text.strip():
            bundles.append(LyricsBundle(
                kind="plain",
                plain=text.strip(),
                source=f"{self.source} | {search_term}",
                audio_duration_ms=(
                    int(round(query.duration * 1000))
                    if query.duration is not None else None
                ),
                confidence=0.5,
                warnings=[f"{elapsed_ms} ms"],
            ))
            logger.info(
                "%s: plain text (%d ms)", self.source, elapsed_ms,
            )
            return bundles

        return bundles

    def _confidence(self, query: SongQuery, validation) -> float:
        """Calculate confidence based on source quality and validation."""
        base = {
            "musixmatch": 0.8,
            "netease": 0.7,
            "megalobiz": 0.6,
            "genius": 0.5,
        }.get(self.source, 0.5)

        # Boost for duration match
        if query.duration and validation.entries:
            last_ts_s = validation.entries[-1][0] / 1000.0
            dur_diff = abs(query.duration - last_ts_s)
            if dur_diff <= 3.0:
                base = min(1.0, base + 0.1)
            elif dur_diff > 15.0:
                base = max(0.3, base - 0.1)

        # Penalize warnings
        if validation.warnings:
            base = max(0.3, base - 0.05 * len(validation.warnings))

        return round(base, 2)


# ── Factory: build all individual providers ────────────────────────

def build_syncedlyrics_providers() -> list[SyncedlyricsProvider]:
    """Return all syncedlyrics providers in quality order.

    Order: Musixmatch → NetEase → Megalobiz → Genius.
    Only returns providers that are available (syncedlyrics installed).
    """
    if not importlib.util.find_spec("syncedlyrics"):
        return []
    sources = ["musixmatch", "netease", "megalobiz", "genius"]
    return [SyncedlyricsProvider(src) for src in sources]
