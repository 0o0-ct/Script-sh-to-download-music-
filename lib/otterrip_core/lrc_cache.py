#!/usr/bin/env python3
"""Local query cache for lyric lookups.

Key: artist + title + duration.  Value: serialised LyricsBundle dict.
Persisted as JSON under a cache directory so repeated lookups for the same
track do not hit the network.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Optional


def _cache_key(artist: str, title: str, duration_ms: Optional[int]) -> str:
    """Stable SHA-256 key for a query."""
    parts = [
        artist.casefold().strip(),
        title.casefold().strip(),
        str(duration_ms) if duration_ms is not None else "",
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


class LyricsCache:
    """Simple filesystem cache keyed by (artist, title, duration)."""

    def __init__(self, cache_dir: str | Path, ttl_seconds: int = 604800):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = ttl_seconds

    def _path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    def get(
        self, artist: str, title: str, duration_ms: Optional[int] = None
    ) -> Optional[dict]:
        """Return cached bundle dict or None if missing / expired."""
        key = _cache_key(artist, title, duration_ms)
        p = self._path(key)
        if not p.is_file():
            return None
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        ts = data.get("cached_at", 0)
        if ts and (os.times().elapsed - ts) > self.ttl_seconds:
            try:
                p.unlink()
            except OSError:
                pass
            return None
        return data.get("bundle")

    def set(
        self,
        artist: str,
        title: str,
        bundle: dict,
        duration_ms: Optional[int] = None,
    ) -> None:
        """Serialise and store a bundle dict."""
        key = _cache_key(artist, title, duration_ms)
        p = self._path(key)
        payload = {
            "cached_at": os.times().elapsed,
            "bundle": bundle,
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