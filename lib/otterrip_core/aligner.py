#!/usr/bin/env python3
"""Forced line-by-line LRC alignment to real audio onsets.

Unlike :mod:`otterrip_core.align` which estimates a single constant offset
for the whole track, this module adjusts **every timestamp individually** by
finding the nearest audio onset within a ±250 ms window.  This corrects
per-line drift that a global offset cannot fix — for example when the
downloaded recording has a slightly different intro or tempo.

Uses ``librosa.onset.onset_detect`` to find the exact onset times in the
audio, then snaps each LRC timestamp to the closest onset.  Lines whose
timestamp already sits near an onset are left unchanged.

librosa is an OPTIONAL dependency (requirements-align.txt).  Without it
the aligner returns the original lines untouched.
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from typing import Optional, Sequence

from otterrip_core.lrc_validator import normalise_lrc, parse_lrc

logger = logging.getLogger("otterrip_core.aligner")

# ── Tunable constants ──────────────────────────────────────────────

SNAP_WINDOW_MS = 250          # only snap within ±250 ms of an onset
MIN_ONSETS = 4                # need enough onsets to trust the alignment
MIN_LINES = 4                 # skip alignment for trivially short lyrics
MAX_SHIFT_PER_LINE_MS = 500   # never move a single line more than this
SR = 22050                    # sample rate for decoding (good enough for onsets)


@dataclass
class AlignReport:
    """Summary of what the aligner did.

    Attributes:
        lines_moved: Number of lines whose timestamp was adjusted.
        total_lines: Total number of timed lines processed.
        max_shift_ms: Largest single-line shift applied.
        mean_shift_ms: Average absolute shift across moved lines.
        note: Human-readable summary (empty on success).
    """

    lines_moved: int = 0
    total_lines: int = 0
    max_shift_ms: int = 0
    mean_shift_ms: float = 0.0
    note: str = ""


# ── Public API ─────────────────────────────────────────────────────

NOTE_NO_LIBROSA = "librosa no instalado — alineación forzada omitida"
NOTE_AUDIO_FAIL = "no se pudo leer el audio para la alineación forzada"
NOTE_TOO_FEW = "pocos onsets o líneas — alineación forzada omitida"


def align_lrc_to_audio(
    lines: Sequence[tuple[int, str]],
    audio_path: str,
    offset_ms: int = 0,
    snap_window_ms: int = SNAP_WINDOW_MS,
    max_shift_ms: int = MAX_SHIFT_PER_LINE_MS,
    min_onsets: int = MIN_ONSETS,
    min_lines: int = MIN_LINES,
) -> tuple[list[tuple[int, str]], AlignReport]:
    """Align LRC line timestamps to the nearest audio onset.

    For each ``(ms, text)`` pair in *lines*, finds the onset detected in
    the audio that falls within ±*snap_window_ms* and moves the timestamp
    to that onset.  Lines already close to an onset are left unchanged.

    Args:
        lines: Sorted list of ``(milliseconds, text)`` tuples.
        audio_path: Path to the audio file on disk.
        offset_ms: Global offset to apply **after** snapping (e.g. from
                   ``LYRICS_OFFSET_MS``).
        snap_window_ms: Maximum distance to an onset for a snap.
        max_shift_ms: Maximum per-line shift allowed.
        min_onsets: Minimum onsets required to proceed.
        min_lines: Minimum lyric lines required to proceed.

    Returns:
        A tuple of ``(aligned_lines, report)``.  When alignment is skipped
        the original lines are returned (possibly shifted by *offset_ms*).
    """
    # ── Check librosa availability ─────────────────────────────────
    try:
        import librosa  # type: ignore
        import numpy as np  # type: ignore
    except Exception:
        report = AlignReport(
            total_lines=len(lines),
            note=NOTE_NO_LIBROSA,
        )
        return _apply_offset(lines, offset_ms), report

    # ── Guard: too few lines ───────────────────────────────────────
    if len(lines) < min_lines:
        report = AlignReport(
            total_lines=len(lines),
            note=NOTE_TOO_FEW,
        )
        return _apply_offset(lines, offset_ms), report

    # ── Load audio and detect onsets ───────────────────────────────
    onset_times_ms = _detect_onsets(audio_path)
    if onset_times_ms is None or len(onset_times_ms) < min_onsets:
        note = NOTE_AUDIO_FAIL if onset_times_ms is None else NOTE_TOO_FEW
        report = AlignReport(
            total_lines=len(lines),
            note=note,
        )
        return _apply_offset(lines, offset_ms), report

    # ── Snap each line to the nearest onset ────────────────────────
    aligned: list[tuple[int, str]] = []
    shifts: list[int] = []

    for ms, txt in lines:
        nearest = _nearest_onset(ms, onset_times_ms, snap_window_ms)
        if nearest is not None:
            shift = nearest - ms
            if abs(shift) <= max_shift_ms:
                aligned.append((max(0, nearest), txt))
                shifts.append(abs(shift))
            else:
                aligned.append((ms, txt))
        else:
            aligned.append((ms, txt))

    # Re-sort to guarantee monotonicity after snapping
    aligned.sort(key=lambda x: x[0])

    # ── Build report ───────────────────────────────────────────────
    lines_moved = len(shifts)
    max_shift = max(shifts) if shifts else 0
    mean_shift = sum(shifts) / len(shifts) if shifts else 0.0

    report = AlignReport(
        lines_moved=lines_moved,
        total_lines=len(lines),
        max_shift_ms=max_shift,
        mean_shift_ms=mean_shift,
    )

    if lines_moved > 0:
        report.note = (
            f"{lines_moved}/{len(lines)} líneas ajustadas "
            f"(max {max_shift} ms, media {mean_shift:.0f} ms)"
        )

    # ── Apply global offset after snapping ─────────────────────────
    final = _apply_offset(aligned, offset_ms)
    return final, report


# ── Internal helpers ───────────────────────────────────────────────


def _detect_onsets(audio_path: str) -> Optional[list[int]]:
    """Detect onset times in *audio_path* and return them in milliseconds.

    Tries ``librosa.load`` first (works for wav/flac/ogg), then falls
    back to ffmpeg decoding for m4a/aac/opus.
    """
    try:
        import librosa  # type: ignore
        import numpy as np  # type: ignore
    except Exception:
        return None

    y = None
    sr = None

    # Try librosa's built-in loader first
    try:
        y, sr = librosa.load(audio_path, sr=SR, mono=True)
    except Exception:
        y, sr = None, None

    # Fallback: decode via ffmpeg (for m4a/aac/opus)
    if y is None or getattr(y, "size", 0) == 0:
        y = _decode_audio_ffmpeg(audio_path)
        sr = SR
        if y is None:
            return None

    try:
        # onset_detect returns times in seconds
        onset_times_s = librosa.onset.onset_detect(
            y=np.asarray(y, dtype=np.float32),
            sr=sr,
            hop_length=512,
            units="time",
        )
        # Convert to milliseconds
        onset_ms = [int(round(t * 1000)) for t in onset_times_s]
        return onset_ms
    except Exception as exc:
        logger.warning("onset_detect failed for %s: %s", audio_path, exc)
        return None


def _decode_audio_ffmpeg(path: str) -> Optional["object"]:  # noqa: F821
    """Decode any audio format to mono float32 via ffmpeg pipe."""
    cmd = [
        "ffmpeg", "-v", "error", "-i", path,
        "-vn", "-map_metadata", "-1",
        "-f", "f32le", "-acodec", "pcm_f32le",
        "-ac", "1", "-ar", str(SR), "pipe:1",
    ]
    try:
        import numpy as np  # type: ignore

        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=120,
        )
        if proc.returncode != 0 or not proc.stdout:
            return None
        y = np.frombuffer(proc.stdout, dtype=np.float32)
        if y.size == 0:
            return None
        return y
    except Exception:
        return None


def _nearest_onset(
    ms: int,
    onsets_ms: list[int],
    window_ms: int,
) -> Optional[int]:
    """Find the onset closest to *ms* within ±*window_ms*.

    Returns the onset in milliseconds, or ``None`` if no onset is close
    enough.
    """
    # Binary search for the insertion point
    lo, hi = 0, len(onsets_ms) - 1
    best = None
    best_dist = window_ms + 1

    # Linear scan from the binary-search insertion point outward
    # is simpler and fast enough for typical onset counts (50-500).
    import bisect  # always available

    idx = bisect.bisect_left(onsets_ms, ms)

    # Check idx and idx-1 (the two closest candidates)
    for candidate_idx in (idx - 1, idx):
        if 0 <= candidate_idx < len(onsets_ms):
            dist = abs(onsets_ms[candidate_idx] - ms)
            if dist < best_dist:
                best_dist = dist
                best = onsets_ms[candidate_idx]

    if best_dist > window_ms:
        return None
    return best


def _apply_offset(
    lines: Sequence[tuple[int, str]],
    offset_ms: int,
) -> list[tuple[int, str]]:
    """Apply a global offset to all lines, clamping to ≥ 0."""
    if offset_ms == 0:
        return list(lines)
    return [(max(0, ms + offset_ms), txt) for ms, txt in lines]


# ── Convenience: LRC string I/O ────────────────────────────────────


def align_lrc_string(
    lrc_text: str,
    audio_path: str,
    offset_ms: int = 0,
    snap_window_ms: int = SNAP_WINDOW_MS,
    max_shift_ms: int = MAX_SHIFT_PER_LINE_MS,
) -> tuple[str, AlignReport]:
    """Align an LRC string to audio onsets and return the corrected string.

    Args:
        lrc_text: LRC-formatted string with ``[mm:ss.xx]`` timestamps.
        audio_path: Path to the audio file.
        offset_ms: Global offset applied after snapping.
        snap_window_ms: Snap window in milliseconds.
        max_shift_ms: Maximum per-line shift.

    Returns:
        Tuple of ``(aligned_lrc_string, report)``.
    """
    lines = parse_lrc(lrc_text)
    if not lines:
        return lrc_text, AlignReport(note="sin líneas con timestamp")

    aligned_lines, report = align_lrc_to_audio(
        lines, audio_path,
        offset_ms=offset_ms,
        snap_window_ms=snap_window_ms,
        max_shift_ms=max_shift_ms,
    )
    aligned_lrc = normalise_lrc(aligned_lines)
    return aligned_lrc, report


# ── Convenience: LyricsBundle integration ──────────────────────────


def align_bundle(
    bundle,
    audio_path: str,
    offset_ms: int = 0,
    snap_window_ms: int = SNAP_WINDOW_MS,
    max_shift_ms: int = MAX_SHIFT_PER_LINE_MS,
) -> AlignReport:
    """Align a :class:`LyricsBundle` in-place to audio onsets.

    Mutates ``bundle.lines`` and ``bundle.lrc``.  Returns an
    :class:`AlignReport` describing what changed.

    Args:
        bundle: A :class:`LyricsBundle` with synced lyrics.
        audio_path: Path to the audio file.
        offset_ms: Global offset applied after snapping.
        snap_window_ms: Snap window in milliseconds.
        max_shift_ms: Maximum per-line shift.
    """
    if not bundle.has_sync or not bundle.lines:
        return AlignReport(note="sin letras sincronizadas")

    aligned_lines, report = align_lrc_to_audio(
        bundle.lines, audio_path,
        offset_ms=offset_ms,
        snap_window_ms=snap_window_ms,
        max_shift_ms=max_shift_ms,
    )

    if report.lines_moved > 0:
        bundle.lines = aligned_lines
        bundle.lrc = normalise_lrc(aligned_lines)
        bundle.offset_ms += offset_ms
        bundle.warnings.append(f"alineación forzada: {report.note}")

    return report
