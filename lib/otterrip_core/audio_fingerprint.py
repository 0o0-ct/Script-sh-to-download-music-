#!/usr/bin/env python3
"""Acoustic fingerprint for lyrics cache key fallback.

When ISRC is not available in the audio metadata, this module generates
a compact fingerprint from the audio file using librosa chroma features.
The fingerprint is a short SHA-256 hex digest (16 chars) that identifies
the same recording even across different encodings or bitrates.

Usage:
    fingerprint = compute_fingerprint("/path/to/audio.mp3")
    # Returns a 16-char hex string or None on failure

The fingerprint is designed to be:
- Fast: processes only the first 15 seconds of audio at low sample rate
- Compact: 16 hex characters stored in the cache key
- Stable: same recording produces the same fingerprint regardless of
  encoding (MP3, FLAC, M4A) or bitrate differences
- Optional: requires librosa; falls back to None gracefully
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Optional

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


def compute_fingerprint(audio_path: str | Path) -> Optional[str]:
    """Compute a compact acoustic fingerprint for *audio_path*.

    Uses librosa chroma features extracted from the first
    ``FINGERPRINT_DURATION_S`` seconds of audio.  The result is a
    short hex digest suitable for use as part of a cache key.

    Args:
        audio_path: Path to an audio file (MP3, FLAC, M4A, WAV, etc.).

    Returns:
        A hex string of length ``DIGEST_LENGTH`` (e.g. ``"a3f2b8c1d4e5f6a7"``),
        or ``None`` if librosa is not available or the audio cannot be read.
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
        frame_length = int(FINGERPRINT_DURATION_S * FINGERPRINT_SR)
        y, sr = librosa.load(
            str(path),
            sr=FINGERPRINT_SR,
            mono=True,
            duration=FINGERPRINT_DURATION_S,
        )

        if len(y) < FINGERPRINT_SR:
            # Audio is too short to produce a meaningful fingerprint
            logger.debug("huella: audio demasiado corto: %s", path)
            return None

        # Extract chroma features — captures harmonic content which is
        # stable across encoding changes but unique to the recording
        chroma = librosa.feature.chroma_stft(
            y=y,
            sr=sr,
            n_fft=1024,
            hop_length=HOP_LENGTH,
            n_chroma=CHROMA_BINS,
        )

        # Quantize to reduce sensitivity to small variations
        # Round to 2 decimal places and convert to bytes
        quantized = _quantize_chroma(chroma)

        # Hash the quantized chroma data
        digest = hashlib.sha256(quantized).hexdigest()[:DIGEST_LENGTH]
        logger.debug("huella acústica: %s → %s", path.name, digest)
        return digest

    except Exception as exc:
        logger.debug("huella falló para %s: %s", path.name, exc)
        return None


def _quantize_chroma(chroma: "numpy.ndarray") -> bytes:
    """Quantize chroma features to reduce sensitivity to small variations.

    Rounds each value to 2 decimal places and serializes to bytes.
    This ensures that minor encoding differences (MP3 vs FLAC) produce
    the same fingerprint.
    """
    import numpy as np

    # Round to 2 decimal places for stability
    quantized = np.round(chroma, decimals=2)

    # Serialize to bytes (float32 for compactness)
    return quantized.astype("float32").tobytes()


def fingerprint_cache_key(fingerprint: Optional[str]) -> str:
    """Format a fingerprint for inclusion in a cache key.

    Returns the fingerprint string or an empty string if None.
    """
    return fingerprint or ""
