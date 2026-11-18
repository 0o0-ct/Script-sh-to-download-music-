#!/usr/bin/env python3
"""LRCLIB provider adapter — robust, validated, retries with backoff."""
from __future__ import annotations

import asyncio
from typing import Optional

import aiohttp

from otterrip_core.lyrics_bundle import LyricsBundle
from otterrip_core.lrc_validator import validate_and_normalise_lrc, normalise_lrc
from otterrip_core.query_clean import (
    build_query_terms,
    split_feat,
    normalize_unicode,
)
from otterrip_core.adapters.base import BaseProvider, SongQuery


class LrclibProvider(BaseProvider):
    """LRCLIB.net adapter — free, no API key required."""

    name = "lrclib"
    enabled_by_default = True
    max_retries = 3
    base_backoff = 1.5  # seconds

    def __init__(
        self,
        host: str = "https://lrclib.net",
        user_agent: str = "OtterRip/7.2.0 (https://github.com/0o0-ct/Script-sh-to-download-music-)",
    ):
        self.host = host.rstrip("/")
        self.user_agent = user_agent

    async def search(self, query: SongQuery) -> list[LyricsBundle]:
        """Search LRCLIB, returning validated LyricsBundle objects.

        Strategy:
        1. Exact match (``/api/get``, with duration) using artist + title.
        2. Filtered search (``/api/search`` with ``track_name``/``artist_name``)
           — tolerant of case, accents and residual junk in the title.
        3. Full-text search (``/api/search?q=``) — catches compound YouTube
           titles like "Canción - Artista - Playlist" after cleaning.
        4. Title-only search — helps when YouTube Music metadata has multiple
           artists (e.g. "Un Corazón, Kim Richards, Marco Barrientos") but
           LRCLIB has the track under a simpler artist name.
        """
        bundles: list[LyricsBundle] = []

        terms = build_query_terms(query.title, query.artist, query.album)
        feat_title, feat_artist = split_feat(query.title)
        if feat_artist:
            terms = build_query_terms(
                query.title, query.artist, query.album, feat_artist
            )

        # 1) Exact match with real audio duration.
        exact = await self._fetch_exact(
            terms["artist"], terms["title"], terms["album"],
            query.duration, query.isrc,
        )
        if exact:
            bundles.append(exact)
        if exact and exact.has_sync:
            return bundles

        # 2) Filtered search — tolerant matching on track + artist.
        if not bundles or not bundles[0].has_sync:
            bundles.extend(await self._fetch_search(
                terms["artist"], terms["title"],
                query.duration, query.isrc,
            ))
        if bundles and bundles[0].has_sync:
            bundles.sort(key=self._rank_key(query.duration))
            return bundles

        # 3) Full-text search with the combined query — tolerant of residual
        #    junk ("Canción - Artista - Playlist") and accent/case drift.
        #    Artist/title still passed for row scoring.
        if not bundles or not bundles[0].has_sync:
            q_text = terms.get("search") or f"{query.artist} {query.title}".strip()
            if q_text:
                bundles.extend(await self._fetch_search(
                    terms["artist"], terms["title"],
                    None, query.isrc, q_text=q_text,
                ))
        if bundles and bundles[0].has_sync:
            bundles.sort(key=self._rank_key(query.duration))
            return bundles

        # 4) Title-only search when the artist match failed (e.g. the tag
        #    artist is the YouTube channel, not the real artist).
        if not bundles:
            bundles.extend(await self._fetch_search(
                "", terms["title"], None, query.isrc,
            ))

        bundles.sort(key=self._rank_key(query.duration))
        return bundles

    # ------------------------------------------------------------------
    # Network
    # ------------------------------------------------------------------

    async def _request_json(
        self,
        session: aiohttp.ClientSession,
        url: str,
        params: dict,
        attempt: int = 0,
    ) -> Optional[dict]:
        """GET JSON with retries.  Distinguishes 404 from retriable errors."""
        try:
            async with session.get(
                url, params=params,
                headers={"User-Agent": self.user_agent},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 404:
                    return None  # permanent — track not found
                if resp.status in (429, 500, 502, 503, 504):
                    if attempt < self.max_retries:
                        wait = self.base_backoff ** (attempt + 1)
                        await asyncio.sleep(wait)
                        return await self._request_json(
                            session, url, params, attempt + 1
                        )
                    return None  # exhausted retries
                if resp.status != 200:
                    return None
                return await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError):
            if attempt < self.max_retries:
                wait = self.base_backoff ** (attempt + 1)
                await asyncio.sleep(wait)
                return await self._request_json(
                    session, url, params, attempt + 1
                )
            return None

    async def _fetch_exact(
        self,
        artist: str,
        title: str,
        album: str,
        duration_s: Optional[float],
        isrc: str,
    ) -> Optional[LyricsBundle]:
        if not artist or not title:
            return None
        params: dict[str, str] = {
            "artist_name": artist,
            "track_name": title,
        }
        if album:
            params["album_name"] = album
        if duration_s is not None:
            params["duration"] = str(int(duration_s))
        if isrc:
            params["isrc"] = isrc

        async with aiohttp.ClientSession() as session:
            data = await self._request_json(
                session, f"{self.host}/api/get", params
            )
        if not data:
            return None
        return self._build_bundle(data, query_duration_s=duration_s)

    async def _fetch_search(
        self,
        artist: str,
        title: str,
        duration_s: Optional[float],
        isrc: str,
        q_text: str = "",
    ) -> list[LyricsBundle]:
        """Tolerant search via ``/api/search``.

        ``q_text`` switches to the full-text endpoint (``?q=``), which
        matches compound/dirty titles that the filtered parameters cannot.
        Rows are scored by artist/title similarity and duration proximity;
        the best few are validated and returned as bundles.

        The album is deliberately NOT sent as a filter: YouTube tags often
        carry the album of a different edition, and it would exclude the
        very row we are looking for.
        """
        if q_text:
            params: dict[str, str] = {"q": q_text}
        else:
            if not title:
                return []
            params = {"track_name": title}
            if artist:
                params["artist_name"] = artist
            if isrc:
                params["isrc"] = isrc

        async with aiohttp.ClientSession() as session:
            data = await self._request_json(
                session, f"{self.host}/api/search", params
            )
        if not isinstance(data, list) or not data:
            return []

        artist_norm = normalize_unicode(artist)
        title_norm = normalize_unicode(title)
        scored = sorted(
            data,
            key=lambda row: self._row_score(
                row, artist_norm, title_norm, duration_s
            ),
            reverse=True,
        )
        bundles: list[LyricsBundle] = []
        seen_sources: set[str] = set()
        for row in scored[:5]:
            bundle = self._build_bundle(row, query_duration_s=duration_s)
            if bundle is None or bundle.source in seen_sources:
                continue
            seen_sources.add(bundle.source)
            bundles.append(bundle)
            if len(bundles) >= 3:
                break
        return bundles

    @staticmethod
    def _row_score(
        row: dict,
        artist_norm: str,
        title_norm: str,
        duration_s: Optional[float],
    ) -> float:
        """Rank a search row: exact artist/title beat partial matches."""
        score = 0.0
        row_artist = normalize_unicode(row.get("artistName") or "")
        row_title = normalize_unicode(
            row.get("trackName") or row.get("name") or ""
        )
        if artist_norm:
            if row_artist == artist_norm:
                score += 2.0
            elif artist_norm in row_artist or row_artist in artist_norm:
                score += 1.0
        if title_norm:
            if row_title == title_norm:
                score += 2.0
            elif title_norm in row_title or row_title in title_norm:
                score += 1.0
        if duration_s and row.get("duration") is not None:
            try:
                if abs(float(row["duration"]) - duration_s) <= 3.0:
                    score += 0.5
            except (TypeError, ValueError):
                pass
        if row.get("syncedLyrics"):
            score += 0.25
        return score

    # ------------------------------------------------------------------
    # Result building
    # ------------------------------------------------------------------

    def _build_bundle(
        self,
        data: dict,
        query_duration_s: Optional[float],
    ) -> Optional[LyricsBundle]:
        if not isinstance(data, dict):
            return None

        synced = data.get("syncedLyrics")
        plain = data.get("plainLyrics")
        instrumental = bool(data.get("instrumental", False))

        if instrumental:
            return LyricsBundle(
                kind="instrumental",
                source=self._source_id(data),
                ref_duration_ms=self._to_ms(data.get("duration")),
                confidence=1.0,
                warnings=["marcada como instrumental en la fuente"],
            )

        duration_ms = self._to_ms(data.get("duration"))
        audio_duration_ms = (
            int(round(query_duration_s * 1000)) if query_duration_s else None
        )

        warnings: list[str] = []

        if audio_duration_ms is not None and duration_ms is not None:
            delta = abs(audio_duration_ms - duration_ms)
            if delta > 3000:
                warnings.append(
                    f"desfase de duración {delta} ms entre audio y fuente"
                )

        if synced:
            validation = validate_and_normalise_lrc(
                synced,
                audio_duration_s=query_duration_s,
            )
            if validation.valid:
                lrc_text = normalise_lrc(validation.entries)
                return LyricsBundle(
                    kind="line",
                    lrc=lrc_text,
                    plain=validation.plain,
                    lines=validation.entries,
                    source=self._source_id(data),
                    audio_duration_ms=audio_duration_ms,
                    ref_duration_ms=duration_ms,
                    confidence=self._confidence(data, query_duration_s),
                    warnings=warnings + validation.warnings,
                )
            else:
                warnings.append(
                    f"letra sincronizada rechazada: {validation.reason}"
                )

        if plain:
            return LyricsBundle(
                kind="plain",
                plain=plain.strip(),
                source=self._source_id(data),
                audio_duration_ms=audio_duration_ms,
                ref_duration_ms=duration_ms,
                confidence=0.4,
                warnings=warnings + ["letra sin sincronizar (plana)"],
            )

        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_ms(value) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(float(value) * 1000)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _source_id(data: dict) -> str:
        name = data.get("name") or data.get("trackName") or ""
        artist = data.get("artistName") or ""
        lrc_id = data.get("id") or data.get("trackId") or ""
        parts = [p for p in ("lrclib", artist, name, str(lrc_id)) if p]
        return " | ".join(parts)

    def _confidence(self, *args, **kwargs) -> float:
        """Flexible confidence calculation.

        Supports both the new signature (_confidence(data, duration_s))
        and the legacy signature (_confidence(name_a, name_b, artist_a, artist_b, duration_s)).
        """
        if len(args) >= 2 and isinstance(args[0], dict):
            # New signature: _confidence(data, query_duration_s)
            data = args[0]
            query_duration_s = args[1] if len(args) > 1 else None
            conf = 0.5
            if data.get("isrc"):
                conf = 0.85
            if data.get("syncedLyrics"):
                conf = max(conf, 0.9)
            if query_duration_s and data.get("duration"):
                try:
                    dur_diff = abs(float(data["duration"]) - query_duration_s)
                    if dur_diff <= 1:
                        conf = min(1.0, conf + 0.1)
                    elif dur_diff > 5:
                        conf = max(0.0, conf - 0.2)
                except (ValueError, TypeError):
                    pass
            return max(0.0, min(1.0, conf))
        else:
            # Legacy signature: _confidence(name_a, name_b, artist_a, artist_b, duration_s)
            return self._confidence_legacy(*args, **kwargs)

    @staticmethod
    def _confidence_legacy(
        name_a: str,
        name_b: str,
        artist_a: str,
        artist_b: str,
        duration_s: Optional[float],
    ) -> float:
        """Legacy confidence: simple fuzzy match of name and artist."""
        name_score = LrclibProvider._fuzzy_match(name_a, name_b)
        artist_score = LrclibProvider._fuzzy_match(artist_a, artist_b)
        return (name_score + artist_score) / 2

    def _rank_key(self, query_duration_s: Optional[float]):
        def key(bundle: LyricsBundle) -> tuple:
            has_sync = 1 if bundle.has_sync else 0
            dur_delta = bundle.duration_delta_ms() or 10_000_000
            return (-has_sync, dur_delta, -bundle.confidence)
        return key


# Backward-compatible re-export for existing tests.
from otterrip_core.lrc_validator import parse_lrc as _parse_lrc  # noqa: E402


# ── Backward-compatible helpers (legacy tests) ──────────────────────


def _fuzzy_match(a: str, b: str) -> float:
    """Simple fuzzy match score [0, 1] (legacy)."""
    if not a or not b:
        return 0.0
    a_lower = a.lower().strip()
    b_lower = b.lower().strip()
    if a_lower == b_lower:
        return 1.0
    if a_lower in b_lower or b_lower in a_lower:
        return 0.8
    tokens_a = set(a_lower.split())
    tokens_b = set(b_lower.split())
    if not tokens_a or not tokens_b:
        return 0.0
    overlap = len(tokens_a & tokens_b)
    union = len(tokens_a | tokens_b)
    return overlap / union if union else 0.0


def _parse_lrc_legacy(text: str) -> list[tuple[float, str]]:
    """Parse LRC text into [(seconds_float, text), ...] (legacy format)."""
    from otterrip_core.lrc_validator import parse_lrc as _parse_ms
    entries_ms = _parse_ms(text)
    return [(ms / 1000.0, txt) for ms, txt in entries_ms]


# Alias the old name for import compatibility.
_parse_lrc = _parse_lrc_legacy
