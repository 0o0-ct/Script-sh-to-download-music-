#!/usr/bin/env python3
"""Local query cache for lyric lookups.

Key: artist + title + duration + ISRC + fingerprint.  Value: serialised
LyricsBundle dict.  Persisted as JSON under a cache directory so repeated
lookups for the same track do not hit the network.

ISRC is optional; when not available the key falls back to an acoustic
fingerprint (computed from the audio file) to avoid collisions between
different versions of the same song.  When neither ISRC nor fingerprint
are available, the key falls back to (artist, title, duration) for
backward compatibility.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Optional


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


class LyricsCache:
    """Simple filesystem cache keyed by (artist, title, duration, ISRC, fingerprint).

    When ISRC is not available, an acoustic fingerprint may be used as a
    fallback to distinguish recordings that share the same title and artist.
    """

    def __init__(self, cache_dir: str | Path, ttl_seconds: int = 604800):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds

    def _path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def _read_entry(self, key: str) -> Optional[dict]:
        """Read and validate a single cache entry by key.

        Returns the bundle dict or None if missing / expired / corrupt.
        """
        p = self._path(key)
        if not p.is_file():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        ts = data.get("cached_at", 0)
        if ts and (time.time() - ts) > self.ttl_seconds:
            try:
                p.unlink()
            except OSError:
                pass
            return None
        return data.get("bundle")

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
        return self._read_entry(key)

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
        """Serialise and store a bundle dict.

        Stores under the most specific key available: ISRC > fingerprint > legacy.
        """
        key = _cache_key(artist, title, duration_ms, isrc, fingerprint)
        p = self._path(key)
        payload = {
            "cached_at": time.time(),
            "bundle": bundle,
            "keyed_by": "isrc" if isrc else ("fingerprint" if fingerprint else "legacy"),
        }
        try:
            p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass  # Cache failure is non-fatal.

    def clear(self) -> int:
        """Remove all cache files.  Returns number of files deleted."""
        count = 0
        for p in self.cache_dir.glob("*.json"):
            try:
                p.unlink()
                count += 1
            except OSError:
                pass
        return count