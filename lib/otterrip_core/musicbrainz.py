#!/usr/bin/env python3
"""MusicBrainz canonical metadata lookup.

Queries the MusicBrainz REST API (musicbrainz.org/ws/2/) to obtain
canonical title, artist, album, duration and ISRC for a given recording.
Acts as the single source of truth before building lyrics queries, so
that covers, live versions and low-quality uploads are less likely to
be matched.

Fallback: returns ``None`` on timeout, network error or no match so
that the caller can fall back to the raw (cleaned) user text.
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import Optional

import aiohttp

logger = logging.getLogger("otterrip_core.musicbrainz")

MB_BASE = "https://musicbrainz.org/ws/2/"
MB_USERAGENT = "OtterRip/7.3.4"


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
    """

    title: str = ""
    artist: str = ""
    album: str = ""
    duration: Optional[float] = None
    isrc: str = ""
    recording_id: str = ""


def _join_artists(recording: dict) -> str:
    """Join artist credits from a MusicBrainz recording node."""
    artists = recording.get("artist-credit", [])
    parts: list[str] = []
    for cred in artists:
        name = cred.get("name", "") if isinstance(cred, dict) else cred
        if name:
            parts.append(name)
    return ", ".join(parts) if parts else ""


def _extract_isrc(recording: dict) -> str:
    """Extract the first ISRC from a recording's isrc-list."""
    isrcs = recording.get("isrcs", [])
    if isrcs:
        return isrcs[0].get("isrc", "").strip()
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


async def lookup_canonical(
    title: str,
    artist: str,
    album: str = "",
    timeout: float = 5.0,
    session: Optional[aiohttp.ClientSession] = None,
) -> Optional[CanonicalMetadata]:
    """Query MusicBrainz for canonical recording metadata.

    Args:
        title: Recording title (may contain YouTube noise).
        artist: Artist name (may contain YouTube noise).
        album: Album / release name (optional, narrows results).
        timeout: HTTP timeout in seconds.  Returns ``None`` on timeout.
        session: Optional shared ``aiohttp.ClientSession``.  A temporary
                 session is created when not provided.

    Returns:
        ``CanonicalMetadata`` with the best-match recording, or ``None``
        when the API is unreachable or no result is found.
    """
    if not title or not artist:
        return None

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
    }

    headers = {"User-Agent": MB_USERAGENT}

    owned = session is None
    if owned:
        session = aiohttp.ClientSession()

    try:
        async with session.get(
            f"{MB_BASE}recording/",
            params=params,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            if resp.status != 200:
                logger.warning(
                    "MusicBrainz returned %d for query '%s'", resp.status, query
                )
                return None

            data = await resp.json()

        recordings = data.get("recordings", [])
        if not recordings:
            logger.debug("MusicBrainz no results for '%s'", query)
            return None

        rec = recordings[0]
        canonical_title = rec.get("title", "").strip()
        canonical_artist = _join_artists(rec)
        duration = _extract_duration(rec)
        isrc = _extract_isrc(rec)
        rec_id = rec.get("id", "")

        # Try to get album from first release
        album_name = ""
        releases = rec.get("releases", [])
        if releases:
            album_name = releases[0].get("title", "").strip()

        # If album was passed in and no album from releases, use the passed one
        if not album_name and album:
            album_name = album

        meta = CanonicalMetadata(
            title=canonical_title or title,
            artist=canonical_artist or artist,
            album=album_name,
            duration=duration,
            isrc=isrc,
            recording_id=rec_id,
        )

        logger.info(
            "MusicBrainz canonical: '%s' by '%s' "
            "(duration=%.1fs, isrc=%s, id=%s)",
            meta.title,
            meta.artist,
            meta.duration or 0,
            meta.isrc or "(none)",
            meta.recording_id or "(none)",
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
    finally:
        if owned and session:
            await session.close()


# -----------------------------------------------------------------------
# Synchronous wrapper for callers that are not async
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
        # Already inside a running loop -- run in executor to avoid blocking
        import concurrent.futures

        futures = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = futures.submit(
            _run_in_new_loop, title, artist, album, timeout
        )
        return future.result(timeout=timeout + 2)
    else:
        return _run_in_new_loop(title, artist, album, timeout)


def _run_in_new_loop(
    title: str,
    artist: str,
    album: str,
    timeout: float,
) -> Optional[CanonicalMetadata]:
    """Run lookup_canonical in a fresh event loop."""
    import asyncio as _aio

    return _aio.run(lookup_canonical(title, artist, album, timeout=timeout))
