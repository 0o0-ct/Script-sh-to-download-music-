#!/usr/bin/env python3
"""MusicBrainz canonical metadata lookup.

Queries the MusicBrainz REST API (musicbrainz.org/ws/2/) to obtain
canonical title, artist, album, duration, ISRC, track number, disc
number, year, label, release ID, and artist IDs for a given recording.
Acts as the single source of truth before building lyrics queries, so
that covers, live versions and low-quality uploads are less likely to
be matched.

Uses a shared rate limiter (~1 req/s) with response caching and
exponential backoff retries. Results are cached in StateDB metadata_cache.

Fallback: returns ``None`` on timeout, network error or no match so
that the caller can fall back to the raw (cleaned) user text.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
from typing import List, Optional

import aiohttp

from otterrip_core.rate_limiter import MusicBrainzRateLimiter

logger = logging.getLogger("otterrip_core.musicbrainz")

MB_BASE = "https://musicbrainz.org/ws/2/"
MB_USERAGENT = "OtterRip/7.5.0"

# Global rate limiter singleton
_rate_limiter: Optional[MusicBrainzRateLimiter] = None


def get_rate_limiter() -> MusicBrainzRateLimiter:
    """Return the module-level rate limiter singleton."""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = MusicBrainzRateLimiter()
    return _rate_limiter


@dataclasses.dataclass
class CanonicalMetadata:
    """Canonical metadata returned by MusicBrainz.

    Attributes:
        title: Official recording title.
        artist: Primary artist name (joined with ", " for groups).
        album: Release/album name (may be empty for singles).
        duration: Duration in seconds (from the recording length field).
        isrc: ISRC code if available on the recording.
        recording_id: MusicBrainz recording MBID for caching/debugging.
        track_number: Track number on the release (1-based).
        disc_number: Disc/medium number (1-based).
        year: Release year (4-digit integer).
        label: Record label name.
        release_id: MusicBrainz release MBID (for Cover Art Archive).
        artist_ids: List of artist MBIDs.
        release_country: Country code where the release was published.
    """

    title: str = ""
    artist: str = ""
    album: str = ""
    duration: Optional[float] = None
    isrc: str = ""
    recording_id: str = ""
    track_number: int = 0
    disc_number: int = 0
    year: int = 0
    label: str = ""
    release_id: str = ""
    artist_ids: List[str] = dataclasses.field(default_factory=list)
    release_country: str = ""

    def to_dict(self) -> dict:
        """Serialize to dict for caching."""
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CanonicalMetadata":
        """Deserialize from dict (cache retrieval)."""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def _join_artists(recording: dict) -> str:
    """Join artist credits from a MusicBrainz recording node.

    Handles both dict-based artist-credit entries and plain strings.
    """
    artists = recording.get("artist-credit", [])
    if not artists:
        return ""
    parts: list[str] = []
    for cred in artists:
        if isinstance(cred, dict):
            name = cred.get("name", "")
            if name:
                parts.append(str(name))
        elif isinstance(cred, str):
            if cred:
                parts.append(cred)
    return ", ".join(parts) if parts else ""


def _extract_artist_ids(recording: dict) -> List[str]:
    """Extract artist MBIDs from artist-credit entries."""
    artists = recording.get("artist-credit", [])
    ids: List[str] = []
    for cred in artists:
        if isinstance(cred, dict):
            aid = cred.get("artist", {}).get("id", "")
            if aid:
                ids.append(str(aid))
    return ids


def _extract_isrc(recording: dict) -> str:
    """Extract the first ISRC from a recording's isrc-list.

    MusicBrainz may return ``isrcs`` as either a list of dicts
    ``[{"isrc": "..."}]`` (when queried by MBID with inc=isrcs) or a
    list of plain strings ``["USXX12345678"]`` (search results).
    """
    isrcs = recording.get("isrcs", [])
    if not isrcs:
        return ""
    first = isrcs[0]
    if isinstance(first, dict):
        return str(first.get("isrc", "")).strip()
    elif isinstance(first, str):
        return first.strip()
    return ""


def _extract_duration(recording: dict) -> Optional[float]:
    """Extract duration in seconds from recording length (microseconds)."""
    length_us = recording.get("length")
    if length_us is not None:
        try:
            return int(length_us) / 1_000_000.0
        except (ValueError, TypeError):
            pass
    return None


def _extract_release_info(recording: dict) -> dict:
    """Extract extended metadata from the first release in a recording.

    Returns a dict with: album, track_number, disc_number, year, label,
    release_id, release_country.
    """
    info: dict = {
        "album": "",
        "track_number": 0,
        "disc_number": 0,
        "year": 0,
        "label": "",
        "release_id": "",
        "release_country": "",
    }

    releases = recording.get("releases", [])
    if not releases:
        return info

    rel = releases[0]
    info["album"] = rel.get("title", "").strip()
    info["release_id"] = rel.get("id", "").strip()
    info["release_country"] = rel.get("country", "").strip()

    # Year from date field (YYYY-MM-DD or YYYY)
    date_str = rel.get("date", "")
    if date_str:
        try:
            info["year"] = int(str(date_str)[:4])
        except (ValueError, TypeError):
            pass

    # Label from p-list
    p_list = rel.get("p-list", [])
    if p_list:
        info["label"] = p_list[0].get("name", "").strip()

    # Track number and disc number from medium-list
    mediums = rel.get("medium-list", [])
    if mediums:
        medium = mediums[0]
        # Disc number is the position + 1
        info["disc_number"] = medium.get("position", 1)

        # Track number from track-list
        tracks = medium.get("track-list", [])
        for track in tracks:
            track_id = track.get("id", "")
            # Match against recording ID
            if track_id == recording.get("id", ""):
                info["track_number"] = track.get("number", 0)
                try:
                    info["track_number"] = int(info["track_number"])
                except (ValueError, TypeError):
                    info["track_number"] = 0
                break

    return info


def _cache_key(title: str, artist: str, album: str = "") -> str:
    """Generate a cache key for a lookup query."""
    import hashlib
    raw = f"{title}|{artist}|{album}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _try_get_cached(key: str) -> Optional[CanonicalMetadata]:
    """Try to retrieve cached metadata from StateDB."""
    try:
        from otterrip_core.state_db import StateDB
        state_dir = os.environ.get("OTTERRIP_STATE_DIR", "")
        if not state_dir:
            return None
        db = StateDB(state_dir)
        try:
            data = db.metadata_cache_get("mb:" + key)
            if data:
                return CanonicalMetadata.from_dict(data)
        finally:
            db.close()
    except Exception:
        pass
    return None


def _try_set_cached(key: str, meta: CanonicalMetadata) -> None:
    """Store metadata in StateDB cache."""
    try:
        from otterrip_core.state_db import StateDB
        state_dir = os.environ.get("OTTERRIP_STATE_DIR", "")
        if not state_dir:
            return
        db = StateDB(state_dir)
        try:
            db.metadata_cache_set("mb:" + key, meta.to_dict())
        finally:
            db.close()
    except Exception:
        pass


async def lookup_canonical(
    title: str,
    artist: str,
    album: str = "",
    timeout: float = 5.0,
    session: Optional[aiohttp.ClientSession] = None,
) -> Optional[CanonicalMetadata]:
    """Query MusicBrainz for canonical recording metadata.

    Uses the shared rate limiter for ~1 req/s, caches results in
    StateDB metadata_cache, and extracts extended metadata including
    track number, disc number, year, label, release ID, and artist IDs.

    Args:
        title: Recording title (may contain YouTube noise).
        artist: Artist name (may contain YouTube noise).
        album: Album / release name (optional, narrows results).
        timeout: HTTP timeout in seconds.  Returns ``None`` on timeout.
        session: Deprecated. The rate limiter manages sessions internally.

    Returns:
        ``CanonicalMetadata`` with the best-match recording, or ``None``
        when the API is unreachable or no result is found.
    """
    if not title or not artist:
        return None

    # Check cache first
    key = _cache_key(title, artist, album)
    cached = _try_get_cached(key)
    if cached is not None:
        logger.debug("Cache hit for MusicBrainz lookup: '%s' by '%s'", title, artist)
        return cached

    # Build query filters
    query_parts: list[str] = []
    query_parts.append(f"recording:{title}")
    query_parts.append(f"artist:{artist}")
    if album:
        query_parts.append(f"release:{album}")
    query = " ".join(query_parts)

    params: dict[str, str] = {
        "query": query,
        "fmt": "json",
        "limit": "1",
        "inc": "recordings+isrcs+artist-credits+releases+release-groups",
    }

    headers = {"User-Agent": MB_USERAGENT}

    limiter = get_rate_limiter()
    try:
        data = await limiter.fetch_json(
            f"{MB_BASE}recording/",
            params=params,
            headers=headers,
        )

        if data is None:
            logger.warning("MusicBrainz returned no data for query '%s'", query)
            return None

        recordings = data.get("recordings", [])
        if not recordings:
            logger.debug("MusicBrainz no results for '%s'", query)
            return None

        rec = recordings[0]
        if not isinstance(rec, dict):
            logger.debug(
                "MusicBrainz returned non-dict recording for '%s': %s",
                query, type(rec).__name__,
            )
            return None

        canonical_title = rec.get("title", "").strip()
        canonical_artist = _join_artists(rec)
        duration = _extract_duration(rec)
        isrc = _extract_isrc(rec)
        rec_id = rec.get("id", "")
        artist_ids = _extract_artist_ids(rec)

        # Extended release info
        rel_info = _extract_release_info(rec)
        album_name = rel_info["album"]
        if not album_name and album:
            album_name = album

        meta = CanonicalMetadata(
            title=canonical_title or title,
            artist=canonical_artist or artist,
            album=album_name,
            duration=duration,
            isrc=isrc,
            recording_id=rec_id,
            track_number=rel_info["track_number"],
            disc_number=rel_info["disc_number"],
            year=rel_info["year"],
            label=rel_info["label"],
            release_id=rel_info["release_id"],
            artist_ids=artist_ids,
            release_country=rel_info["release_country"],
        )

        # Cache the result
        _try_set_cached(key, meta)

        logger.info(
            "MusicBrainz canonical: '%s' by '%s' "
            "(duration=%.1fs, isrc=%s, id=%s, track=%d, year=%d)",
            meta.title,
            meta.artist,
            meta.duration or 0,
            meta.isrc or "(none)",
            meta.recording_id or "(none)",
            meta.track_number,
            meta.year,
        )
        return meta

    except asyncio.TimeoutError:
        logger.warning("MusicBrainz timeout (%.1fs) for '%s'", timeout, query)
        return None
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("MusicBrainz error for '%s': %s", query, exc)
        return None


async def lookup_by_isrc(
    isrc: str,
    timeout: float = 5.0,
) -> Optional[CanonicalMetadata]:
    """Query MusicBrainz for a recording by ISRC code.

    Args:
        isrc: ISRC code (e.g. "USXX12345678").
        timeout: HTTP timeout in seconds.

    Returns:
        ``CanonicalMetadata`` if found, or ``None``.
    """
    if not isrc:
        return None

    # Check cache
    key = _cache_key("", "", isrc)
    cached = _try_get_cached(key)
    if cached is not None:
        logger.debug("Cache hit for ISRC lookup: %s", isrc)
        return cached

    params: dict[str, str] = {
        "isrc": isrc,
        "fmt": "json",
        "inc": "recordings+isrcs+artist-credits+releases+release-groups",
    }

    headers = {"User-Agent": MB_USERAGENT}

    limiter = get_rate_limiter()
    try:
        data = await limiter.fetch_json(
            f"{MB_BASE}isrc/",
            params=params,
            headers=headers,
        )

        if data is None:
            return None

        recordings = data.get("recordings", [])
        if not recordings:
            logger.debug("MusicBrainz no results for ISRC '%s'", isrc)
            return None

        rec = recordings[0]
        if not isinstance(rec, dict):
            return None

        canonical_title = rec.get("title", "").strip()
        canonical_artist = _join_artists(rec)
        duration = _extract_duration(rec)
        rec_isrc = _extract_isrc(rec)
        rec_id = rec.get("id", "")
        artist_ids = _extract_artist_ids(rec)

        rel_info = _extract_release_info(rec)
        album_name = rel_info["album"]

        meta = CanonicalMetadata(
            title=canonical_title,
            artist=canonical_artist,
            album=album_name,
            duration=duration,
            isrc=rec_isrc,
            recording_id=rec_id,
            track_number=rel_info["track_number"],
            disc_number=rel_info["disc_number"],
            year=rel_info["year"],
            label=rel_info["label"],
            release_id=rel_info["release_id"],
            artist_ids=artist_ids,
            release_country=rel_info["release_country"],
        )

        _try_set_cached(key, meta)

        logger.info(
            "MusicBrainz ISRC lookup: '%s' by '%s' (isrc=%s)",
            meta.title, meta.artist, meta.isrc,
        )
        return meta

    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.warning("MusicBrainz ISRC error for '%s': %s", isrc, exc)
        return None


# -----------------------------------------------------------------------
# Synchronous wrappers for callers that are not async
# -----------------------------------------------------------------------

def lookup_canonical_sync(
    title: str,
    artist: str,
    album: str = "",
    timeout: float = 5.0,
) -> Optional[CanonicalMetadata]:
    """Synchronous wrapper around :func:`lookup_canonical`.

    Runs the async function in a new event loop.  Use this from
    synchronous code paths (e.g., the Bash wrapper calling Python
    directly without an existing loop).
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        import concurrent.futures
        futures = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = futures.submit(
            _run_in_new_loop, title, artist, album, timeout
        )
        return future.result(timeout=timeout + 2)
    else:
        return _run_in_new_loop(title, artist, album, timeout)


def lookup_by_isrc_sync(
    isrc: str,
    timeout: float = 5.0,
) -> Optional[CanonicalMetadata]:
    """Synchronous wrapper around :func:`lookup_by_isrc`."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None:
        import concurrent.futures
        futures = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = futures.submit(_run_isrc_loop, isrc, timeout)
        return future.result(timeout=timeout + 2)
    else:
        return _run_isrc_loop(isrc, timeout)


def _run_in_new_loop(
    title: str,
    artist: str,
    album: str,
    timeout: float,
) -> Optional[CanonicalMetadata]:
    """Run lookup_canonical in a fresh event loop."""
    import asyncio as _aio
    return _aio.run(lookup_canonical(title, artist, album, timeout=timeout))


def _run_isrc_loop(
    isrc: str,
    timeout: float,
) -> Optional[CanonicalMetadata]:
    """Run lookup_by_isrc in a fresh event loop."""
    import asyncio as _aio
    return _aio.run(lookup_by_isrc(isrc, timeout=timeout))
