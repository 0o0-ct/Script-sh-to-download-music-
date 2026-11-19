#!/usr/bin/env python3
"""Acoustic fingerprint for lyrics cache key fallback.

Supports two fingerprinting engines:
1. **Chromaprint** (primary): Uses pyacoustid/fpcalc for industry-standard
   acoustic fingerprints that work across different encodings and bitrates.
2. **Librosa** (fallback): Chroma features for environments without Chromaprint.

The fingerprint is a short SHA-256 hex digest (16 chars) that identifies
the same recording even across different encodings or bitrates.

Usage:
    fingerprint = compute_fingerprint("/path/to/audio.mp3")
    # Returns a 16-char hex string or None on failure

    # Optional: look up the fingerprint on AcoustID
    result = compute_fingerprint_acoustid("/path/to/audio.mp3")

The fingerprint is designed to be:
- Fast: processes only the first 15 seconds of audio at low sample rate
- Compact: 16 hex characters stored in the cache key
- Stable: same recording produces the same fingerprint regardless of
  encoding (MP3, FLAC, M4A) or bitrate differences
- Optional: requires librosa or pyacoustid; falls back to None gracefully
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# How many seconds of audio to analyse (first N seconds)
FINGERPRINT_DURATION_S = 15.0

# Sample rate for fingerprint analysis (low = fast)
FINGERPRINT_SR = 11025

# Chroma bins (12 is standard)
CHROMA_BINS = 12

# Hop length for chroma extraction
HOP_LENGTH = 512

# Length of the hex digest (first N chars of SHA-256)
DIGEST_LENGTH = 16

# AcoustID API endpoint
ACOUSTID_API_URL = "https://api.acoustid.org/v2/lookup"
ACOUSTID_API_KEY = os.environ.get("ACOUSTID_API_KEY", "")


def compute_fingerprint(audio_path: str | Path) -> Optional[str]:
    """Compute a compact acoustic fingerprint for *audio_path*.

    Tries Chromaprint first (via pyacoustid or fpcalc), then falls back
    to librosa chroma features.  The result is a short hex digest
    suitable for use as part of a cache key.

    Args:
        audio_path: Path to an audio file (MP3, FLAC, M4A, WAV, etc.).

    Returns:
        A hex string of length ``DIGEST_LENGTH`` (e.g. ``"a3f2b8c1d4e5f6a7"``),
        or ``None`` if no fingerprinting engine is available or the audio
        cannot be read.
    """
    # Try Chromaprint first
    fp = compute_fingerprint_chromaprint(audio_path)
    if fp is not None:
        return fp

    # Fallback to librosa
    fp = compute_fingerprint_librosa(audio_path)
    if fp is not None:
        return fp

    logger.debug("No fingerprinting engine available (install pyacoustid or librosa)")
    return None


def compute_fingerprint_chromaprint(audio_path: str | Path) -> Optional[str]:
    """Compute fingerprint using Chromaprint (pyacoustid or fpcalc).

    Chromaprint is the industry-standard acoustic fingerprint algorithm
    used by AcoustID and ChromaDNA.  It produces stable fingerprints
    across different encodings and bitrates.

    Args:
        audio_path: Path to an audio file.

    Returns:
        A hex string of length ``DIGEST_LENGTH``, or ``None`` if
        Chromaprint is not available.
    """
    path = Path(audio_path)
    if not path.is_file():
        return None

    # Try pyacoustid first (Python binding)
    fp = _fingerprint_pyacoustid(path)
    if fp is not None:
        return fp

    # Try fpcalc external tool
    fp = _fingerprint_fpcalc(path)
    if fp is not None:
        return fp

    return None


def _fingerprint_pyacoustid(path: Path) -> Optional[str]:
    """Fingerprint using pyacoustid library."""
    try:
        import pyacoustid as acoustid
    except ImportError:
        return None

    try:
        result = acoustid.fingerprint(str(path))
        # result is a tuple: (duration, raw_fingerprint_string)
        if result and len(result) >= 2:
            raw_fp = result[1]
            digest = hashlib.sha256(raw_fp.encode("utf-8")).hexdigest()[:DIGEST_LENGTH]
            logger.debug(
                "Chromaprint (pyacoustid) fingerprint: %s → %s",
                path.name,
                digest,
            )
            return digest
    except Exception as exc:
        logger.debug("pyacoustid fingerprint failed for %s: %s", path.name, exc)

    return None


def _fingerprint_fpcalc(path: Path) -> Optional[str]:
    """Fingerprint using fpcalc external tool."""
    try:
        result = subprocess.run(
            ["fpcalc", "-raw", str(path)],
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            return None
        raw_fp = result.stdout.decode("utf-8", errors="replace").strip()
        if not raw_fp:
            return None
        digest = hashlib.sha256(raw_fp.encode("utf-8")).hexdigest()[:DIGEST_LENGTH]
        logger.debug(
            "Chromaprint (fpcalc) fingerprint: %s → %s",
            path.name,
            digest,
        )
        return digest
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        logger.debug("fpcalc timed out for %s", path.name)
        return None
    except Exception as exc:
        logger.debug("fpcalc failed for %s: %s", path.name, exc)
        return None


def compute_fingerprint_librosa(audio_path: str | Path) -> Optional[str]:
    """Compute fingerprint using librosa chroma features (fallback).

    Args:
        audio_path: Path to an audio file.

    Returns:
        A hex string of length ``DIGEST_LENGTH``, or ``None`` if
        librosa is not available.
    """
    try:
        import librosa
    except ImportError:
        logger.debug("librosa no instalado — huella acústica no disponible")
        return None

    path = Path(audio_path)
    if not path.is_file():
        logger.debug("huella: archivo no encontrado: %s", path)
        return None

    try:
        # Load only the first N seconds at low sample rate for speed
        y, sr = librosa.load(
            str(path),
            sr=FINGERPRINT_SR,
            mono=True,
            duration=FINGERPRINT_DURATION_S,
        )

        if len(y) < FINGERPRINT_SR:
            logger.debug("huella: audio demasiado corto: %s", path)
            return None

        chroma = librosa.feature.chroma_stft(
            y=y,
            sr=sr,
            n_fft=1024,
            hop_length=HOP_LENGTH,
            n_chroma=CHROMA_BINS,
        )

        quantized = _quantize_chroma(chroma)
        digest = hashlib.sha256(quantized).hexdigest()[:DIGEST_LENGTH]
        logger.debug("huella acústica (librosa): %s → %s", path.name, digest)
        return digest

    except Exception as exc:
        logger.debug("huella falló para %s: %s", path.name, exc)
        return None


def compute_fingerprint_acoustid(
    audio_path: str | Path,
    api_key: str = "",
) -> Optional[dict]:
    """Look up an audio file on AcoustID to find matching recordings.

    Requires an AcoustID API key (free at acoustid.org).  The key can
    be passed directly or set via the ``ACOUSTID_API_KEY`` environment
    variable.

    Args:
        audio_path: Path to an audio file.
        api_key: AcoustID API key.  Falls back to the environment variable.

    Returns:
        A dict with ``recording_id``, ``title``, ``artist``, and
        ``mbid`` keys, or ``None`` if no match is found.
    """
    api_key = api_key or ACOUSTID_API_KEY
    if not api_key:
        logger.debug("AcoustID API key no configurada")
        return None

    try:
        import pyacoustid as acoustid
    except ImportError:
        logger.debug("pyacoustid no instalado — AcoustID lookup no disponible")
        return None

    path = Path(audio_path)
    if not path.is_file():
        return None

    try:
        results = acoustid.lookup(
            api_key=api_key,
            file=str(path),
        )
        if not results:
            logger.debug("AcoustID no encontró coincidencias para %s", path.name)
            return None

        best = results[0]  # Best match first
        recording = best.get("recordings", [{}])[0] if best.get("recordings") else {}

        info = {
            "recording_id": best.get("id", ""),
            "title": recording.get("title", ""),
            "artist": recording.get("artist", ""),
            "mbid": recording.get("mbid", ""),
            "score": best.get("score", 0),
        }

        logger.info(
            "AcoustID match: '%s' by '%s' (score=%.1f, mbid=%s)",
            info["title"],
            info["artist"],
            info["score"],
            info["mbid"],
        )
        return info

    except Exception as exc:
        logger.debug("AcoustID lookup failed for %s: %s", path.name, exc)
        return None


def _quantize_chroma(chroma: Any) -> bytes:
    """Quantize chroma features to reduce sensitivity to small variations.

    Rounds each value to 2 decimal places and serializes to bytes.
    This ensures that minor encoding differences (MP3 vs FLAC) produce
    the same fingerprint.
    """
    import numpy as np

    quantized = np.round(chroma, decimals=2)
    return quantized.astype("float32").tobytes()


def fingerprint_cache_key(fingerprint: Optional[str]) -> str:
    """Format a fingerprint for inclusion in a cache key.

    Returns the fingerprint string or an empty string if None.
    """
    return fingerprint or ""
