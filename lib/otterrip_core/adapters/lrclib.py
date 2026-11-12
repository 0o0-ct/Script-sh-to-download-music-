#!/usr/bin/env python3
"""LRCLIB provider adapter."""
from __future__ import annotations

import re
from typing import Optional

import aiohttp

from otterrip_core.models import LyricsResult, SyncLevel
from otterrip_core.adapters.base import BaseProvider, SongQuery


def _parse_lrc(text: str) -> list[tuple[float, str]]:
    """Parse LRC text into timed entries."""
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


class LrclibProvider(BaseProvider):
    """LRCLIB.net adapter — free, no API key required."""

    name = "lrclib"
    enabled_by_default = True

    def __init__(self, host: str = "https://lrclib.net"):
        self.host = host.rstrip("/")

    async def search(self, query: SongQuery) -> list[LyricsResult]:
        results: list[LyricsResult] = []
        try:
            async with aiohttp.ClientSession() as session:
                # Try exact search first
                url = f"{self.host}/api/get"
                params: dict[str, str] = {
                    "artist_name": query.artist,
                    "track_name": query.title,
                }
                if query.album:
                    params["album_name"] = query.album
                if query.duration:
                    params["duration"] = str(int(query.duration))
                if query.isrc:
                    params["isrc"] = query.isrc

                async with session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status != 200:
                        return results
                    data = await resp.json()

                if isinstance(data, list):
                    data = data[0] if data else {}
                if not isinstance(data, dict):
                    return results

                sync_level = SyncLevel.NONE
                plain_text = ""
                timed_entries: list[tuple[float, str]] = []
                original_format = "json"

                # Check for synced lyrics
                synced = data.get("syncedLyrics")
                plain = data.get("plainLyrics")

                if synced:
                    sync_level = SyncLevel.LINES
                    timed_entries = _parse_lrc(synced)
                    plain_text = "\n".join(txt for _, txt in timed_entries)
                    original_format = "lrc"
                elif plain:
                    plain_text = plain
                    sync_level = SyncLevel.NONE
                    original_format = "txt"

                # Check for advanced sync (word-level)
                advanced = data.get("instrumental", False)
                if not advanced and data.get("duration"):
                    pass  # duration available for validation

                duration = data.get("duration")
                if isinstance(duration, (int, float)):
                    pass  # already a number
                else:
                    try:
                        duration = float(duration) if duration else None
                    except (ValueError, TypeError):
                        duration = None

                # Calculate confidence based on match quality
                confidence = 0.5
                if data.get("isrc") and query.isrc and data["isrc"] == query.isrc:
                    confidence = 1.0
                elif data.get("name") and data.get("artistName"):
                    name_match = self._fuzzy_match(data.get("name", ""), query.title)
                    artist_match = self._fuzzy_match(data.get("artistName", ""), query.artist)
                    confidence = (name_match + artist_match) / 2
                    if duration and query.duration:
                        dur_diff = abs(duration - query.duration)
                        if dur_diff <= 3:
                            confidence = min(1.0, confidence + 0.2)
                        elif dur_diff > 15:
                            confidence = max(0, confidence - 0.3)

                lang = data.get("lang", "")
                if lang and lang != "eng" and query.language and lang.lower() != query.language.lower():
                    confidence -= 0.1

                results.append(LyricsResult(
                    provider=self.name,
                    title=data.get("name", query.title),
                    artist=data.get("artistName", query.artist),
                    album=data.get("albumName", query.album),
                    duration=duration,
                    language=lang,
                    sync_level=sync_level,
                    plain_text=plain_text,
                    timed_entries=timed_entries,
                    original_format=original_format,
                    confidence=max(0, min(1.0, confidence)),
                    isrc=data.get("isrc", ""),
                ))

        except Exception:
            pass  # Provider failure — don't crash

        return results

    @classmethod
    def max_sync_level(cls) -> SyncLevel:
        return SyncLevel.LINES

    @staticmethod
    def _fuzzy_match(a: str, b: str) -> float:
        """Simple fuzzy match score [0, 1]."""
        if not a or not b:
            return 0.0
        a_lower = a.lower().strip()
        b_lower = b.lower().strip()
        if a_lower == b_lower:
            return 1.0
        # Check containment
        if a_lower in b_lower or b_lower in a_lower:
            return 0.8
        # Token overlap
        tokens_a = set(a_lower.split())
        tokens_b = set(b_lower.split())
        if not tokens_a or not tokens_b:
            return 0.0
        overlap = len(tokens_a & tokens_b)
        union = len(tokens_a | tokens_b)
        return overlap / union if union else 0.0
