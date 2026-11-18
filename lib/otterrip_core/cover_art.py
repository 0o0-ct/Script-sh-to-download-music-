#!/usr/bin/env python3
"""Cover Art Archive integration for OtterRip.

Downloads album cover art from coverartarchive.org using a MusicBrainz
release ID, caches it in StateDB metadata_cache, and embeds it in audio
files using mutagen.

Usage:
    cover_data = fetch_cover_art(release_id, size=250)
    cache_cover_art(release_id, cover_data)
    cover = get_cached_cover(release_id)
    embed_cover(audio_file, cover_data)
"""
from __future__ import annotations

import base64
import io
import logging
import os
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

COVERART_BASE = "https://coverartarchive.org/release/"
COVERART_SIZES = ("thumbnail", "250", "500", "archive")


def fetch_cover_art(
    release_id: str,
    size: str = "250",
    timeout: float = 10.0,
) -> Optional[bytes]:
    """Download cover art from Cover Art Archive.

    Args:
        release_id: MusicBrainz release MBID.
        size: Image size — "thumbnail" (100x100), "250", "500", or "archive" (full).
        timeout: HTTP timeout in seconds.

    Returns:
        Raw image bytes (JPEG/PNG), or ``None`` on failure.
    """
    if not release_id:
        logger.debug("No release_id provided for cover art")
        return None

    if size not in COVERART_SIZES:
        logger.warning("Unknown cover art size '%s', using '250'", size)
        size = "250"

    url = f"{COVERART_BASE}{release_id}/{size}"
    logger.debug("Fetching cover art from %s", url)

    try:
        import urllib.request
        import urllib.error

        req = urllib.request.Request(url)
        req.add_header("User-Agent", "OtterRip/7.5.0")

        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            if not data:
                logger.debug("Empty cover art response for %s", release_id)
                return None
            logger.info(
                "Downloaded cover art for %s (%d bytes, size=%s)",
                release_id, len(data), size,
            )
            return data

    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            logger.debug("No cover art found for release %s", release_id)
        else:
            logger.warning("HTTP %d fetching cover art for %s", exc.code, release_id)
        return None
    except Exception as exc:
        logger.warning("Failed to fetch cover art for %s: %s", release_id, exc)
        return None


def cache_cover_art(release_id: str, data: bytes) -> bool:
    """Store cover art in StateDB metadata_cache.

    Args:
        release_id: MusicBrainz release MBID.
        data: Raw image bytes.

    Returns:
        True if cached successfully.
    """
    try:
        from otterrip_core.state_db import StateDB
        state_dir = os.environ.get("OTTERRIP_STATE_DIR", "")
        if not state_dir:
            logger.debug("OTTERRIP_STATE_DIR not set, skipping cache")
            return False

        db = StateDB(state_dir)
        try:
            # Store as base64 to avoid BLOB issues
            b64 = base64.b64encode(data).decode("ascii")
            cover_info = {
                "release_id": release_id,
                "data_b64": b64,
                "size_bytes": len(data),
                "cached_at": time.time(),
            }
            db.metadata_cache_set("cover:" + release_id, cover_info)
            logger.debug("Cached cover art for %s", release_id)
            return True
        finally:
            db.close()
    except Exception as exc:
        logger.warning("Failed to cache cover art for %s: %s", release_id, exc)
        return False


def get_cached_cover(release_id: str) -> Optional[bytes]:
    """Retrieve cached cover art from StateDB.

    Args:
        release_id: MusicBrainz release MBID.

    Returns:
        Raw image bytes, or ``None`` if not cached.
    """
    try:
        from otterrip_core.state_db import StateDB
        state_dir = os.environ.get("OTTERRIP_STATE_DIR", "")
        if not state_dir:
            return None

        db = StateDB(state_dir)
        try:
            info = db.metadata_cache_get("cover:" + release_id)
            if info is None:
                return None
            b64 = info.get("data_b64", "")
            data = base64.b64decode(b64) if b64 else b""
            logger.debug("Retrieved cached cover art for %s (%d bytes)", release_id, len(data))
            return data
        finally:
            db.close()
    except Exception as exc:
        logger.warning("Failed to retrieve cached cover for %s: %s", release_id, exc)
        return None


def embed_cover(audio_path: str | Path, cover_data: bytes) -> bool:
    """Embed cover art into an audio file using mutagen.

    Supports MP3 (ID3), FLAC (metadata block), and M4A (cover atom).

    Args:
        audio_path: Path to the audio file.
        cover_data: Raw image bytes (JPEG/PNG).

    Returns:
        True if embedded successfully.
    """
    path = Path(audio_path)
    if not path.is_file():
        logger.debug("Audio file not found: %s", path)
        return False

    if not cover_data:
        logger.debug("No cover data to embed")
        return False

    try:
        from mutagen.mp3 import MP3
        from mutagen.id3 import ID3, APIC, error as id3_error
        from mutagen.flac import FLAC
        from mutagen.mp4 import MP4
        from mutagen.easymp4 import EasyMP4

        ext = path.suffix.lower()

        if ext in (".mp3",):
            try:
                audio = MP3(str(path))
            except Exception:
                try:
                    audio = ID3(str(path))
                except id3_error:
                    audio = ID3()
                    audio.addfiley(str(path))

            if "APIC:Cover" not in audio and "APIC:" not in audio:
                # Try to remove existing covers
                for key in list(audio.keys()):
                    if key.startswith("APIC:"):
                        del audio[key]

            # Detect image type
            mime = "image/jpeg"
            if cover_data.startswith(b"\x89PNG"):
                mime = "image/png"

            audio.add(
                APIC(
                    encoding=3,
                    mime=mime,
                    type=3,  # Cover image
                    desc="Cover",
                    data=cover_data,
                )
            )
            audio.save()

        elif ext in (".flac",):
            audio = FLAC(str(path))
            # Remove existing covers
            for tag in list(audio.pictures):
                audio.delete_pictures()
                break
            audio.add_picture(
                _make_picture(cover_data)
            )
            audio.save()

        elif ext in (".m4a", ".m4p"):
            audio = MP4(str(path))
            audio["covr"] = [cover_data]
            audio.save()

        else:
            logger.debug("Unsupported format for cover embed: %s", ext)
            return False

        logger.info("Embedded cover art in %s", path.name)
        return True

    except ImportError:
        logger.debug("mutagen not available for cover embed")
        return False
    except Exception as exc:
        logger.warning("Failed to embed cover in %s: %s", path.name, exc)
        return False


def _make_picture(data: bytes):
    """Create a mutagen FLAC picture from raw image data."""
    try:
        from mutagen.flac import Picture
        pic = Picture()
        pic.type = 3  # Cover
        pic.desc = "Cover"
        pic.mime = "image/jpeg"
        if data.startswith(b"\x89PNG"):
            pic.mime = "image/png"
        pic.data = data
        return pic
    except Exception:
        raise


def fetch_and_cache_cover(
    release_id: str,
    size: str = "250",
    timeout: float = 10.0,
) -> Optional[bytes]:
    """Fetch cover art and cache it in one step.

    Args:
        release_id: MusicBrainz release MBID.
        size: Image size.
        timeout: HTTP timeout.

    Returns:
        Raw image bytes, or ``None``.
    """
    # Try cache first
    cached = get_cached_cover(release_id)
    if cached is not None:
        return cached

    # Fetch from network
    data = fetch_cover_art(release_id, size, timeout)
    if data is not None:
        cache_cover_art(release_id, data)
    return data
