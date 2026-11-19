#!/usr/bin/env python3
"""Local query cache for lyric lookups.

Key: artist + title + duration + ISRC + fingerprint.  Value: serialised
LyricsBundle dict.  Persisted in SQLite (StateDB) so repeated lookups
for the same track do not hit the network.

ISRC is optional; when not available the key falls back to an acoustic
fingerprint (computed from the audio file) to avoid collisions between
different versions of the same song.  When neither ISRC nor fingerprint
are available, the key falls back to (artist, title, duration) for
backward compatibility.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Optional

from otterrip_core.state_db import StateDB


def _cache_key(
    artist: str,
    title: str,
    duration_ms: Optional[int] = None,
    isrc: str = "",
    fingerprint: str = "",
) -> str:
    """Stable SHA-256 key for a query.

    Includes ISRC when available to avoid collisions between songs with
    the same name but different versions (remix, live, etc.).  When ISRC
    is not available, an acoustic *fingerprint* may be used as a fallback
    to distinguish recordings that share the same title and artist.
    """
    parts = [
        artist.casefold().strip(),
        title.casefold().strip(),
        str(duration_ms) if duration_ms is not None else "",
        isrc.casefold().strip() if isrc else "",
        fingerprint.casefold().strip() if fingerprint else "",
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def _get_db() -> StateDB:
    """Return a StateDB instance from OTTERRIP_STATE_DIR."""
    state_dir = os.environ.get("OTTERRIP_STATE_DIR", "")
    if not state_dir:
        raise RuntimeError("OTTERRIP_STATE_DIR not set")
    return StateDB(state_dir)


class LyricsCache:
    """Lyrics cache backed by StateDB (SQLite).

    When ISRC is not available, an acoustic fingerprint may be used as a
    fallback to distinguish recordings that share the same title and artist.
    """

    def __init__(self, cache_dir: str | Path, ttl_seconds: int = 604800):
        self.cache_dir = Path(cache_dir)
        self.ttl_seconds = ttl_seconds
        # Ensure OTTERRIP_STATE_DIR is set so StateDB can be initialised.
        # Fall back to the cache_dir parent if the env var is not set.
        if not os.environ.get("OTTERRIP_STATE_DIR"):
            os.environ["OTTERRIP_STATE_DIR"] = str(self.cache_dir.parent)
        self._db: Optional[StateDB] = None

    @property
    def db(self) -> StateDB:
        if self._db is None:
            self._db = _get_db()
        return self._db

    def get(
        self,
        artist: str,
        title: str,
        duration_ms: Optional[int] = None,
        isrc: str = "",
        fingerprint: str = "",
    ) -> Optional[dict]:
        """Return cached bundle dict or None if missing / expired.

        When both *isrc* and *fingerprint* are empty, falls back to the
        legacy (artist, title, duration) key for backward compatibility.
        """
        key = _cache_key(artist, title, duration_ms, isrc, fingerprint)
        return self.db.lyrics_cache_get(key, ttl_seconds=self.ttl_seconds)

    def get_with_fingerprint(
        self,
        artist: str,
        title: str,
        duration_ms: Optional[int] = None,
        isrc: str = "",
        fingerprint: str = "",
    ) -> Optional[dict]:
        """Look up cache trying ISRC first, then fingerprint, then legacy key.

        This method provides a fallback chain so that a cache entry stored
        with an ISRC key can still be found when only a fingerprint is
        available, and vice versa.
        """
        # Try the most specific key first
        if isrc:
            result = self.get(artist, title, duration_ms, isrc)
            if result is not None:
                return result
        if fingerprint:
            result = self.get(artist, title, duration_ms, "", fingerprint)
            if result is not None:
                return result
        # Fall back to legacy key (no ISRC, no fingerprint)
        return self.get(artist, title, duration_ms)

    def set(
        self,
        artist: str,
        title: str,
        bundle: dict,
        duration_ms: Optional[int] = None,
        isrc: str = "",
        fingerprint: str = "",
    ) -> None:
        """Store a bundle dict in the SQLite cache.

        Stores under the most specific key available: ISRC > fingerprint > legacy.
        """
        key = _cache_key(artist, title, duration_ms, isrc, fingerprint)
        keyed_by = "isrc" if isrc else ("fingerprint" if fingerprint else "legacy")
        self.db.lyrics_cache_set(key, bundle, keyed_by=keyed_by)

    def clear(self) -> int:
        """Remove all cache entries.  Returns number of entries deleted."""
        return self.db.lyrics_cache_clear()
