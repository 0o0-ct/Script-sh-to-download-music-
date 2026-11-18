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
NOTE_NO_OFFSET = "sin offset global detectable"
NOTE_OFFSET_APPLIED = "offset global detectado y aplicado"

# ── Global offset detection constants ──────────────────────────────
MIN_GLOBAL_OFFSET_MS = 500    # minimum offset to consider (ms)
MIN_CONSISTENT_LINES = 3      # minimum lines to confirm consistency
MAX_CONSISTENCY_DEVIATION_MS = 300  # max deviation between line offsets


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


# ── Global offset detection ────────────────────────────────────────


def detect_global_offset(
    lines: Sequence[tuple[int, str]],
    audio_path: str,
    min_offset_ms: int = MIN_GLOBAL_OFFSET_MS,
    min_consistent_lines: int = MIN_CONSISTENT_LINES,
    max_deviation_ms: int = MAX_CONSISTENCY_DEVIATION_MS,
    search_window_ms: int = 2000,
) -> tuple[int, str]:
    """Detect a constant global offset between LRC timestamps and audio onsets.

    When an LRC file comes from a different source (e.g. LRCLIB) than the
    downloaded audio (e.g. YouTube), the entire lyric may be shifted by a
    constant amount — for example, the LRC starts at 0:00 but the vocal
    in the audio starts at 0:02.

    This function compares the first few LRC line timestamps with the
    nearest audio onsets.  If the per-line offsets are all similar
    (within *max_deviation_ms*) and the median offset exceeds
    *min_offset_ms*, returns the detected offset.  Otherwise returns 0.

    Args:
        lines: Sorted list of ``(milliseconds, text)`` tuples.
        audio_path: Path to the audio file on disk.
        min_offset_ms: Minimum absolute offset to consider applying.
        min_consistent_lines: Minimum number of lines that must agree.
        max_deviation_ms: Maximum deviation between individual line offsets.
        search_window_ms: Window to search for the nearest onset per line.

    Returns:
        A tuple of ``(offset_ms, note)``.  When offset is 0, *note*
        explains why no offset was applied.
    """
    if len(lines) < min_consistent_lines:
        return 0, NOTE_NO_OFFSET

    # ── Detect onsets ──────────────────────────────────────────────
    onset_times_ms = _detect_onsets(audio_path)
    if onset_times_ms is None or len(onset_times_ms) < MIN_ONSETS:
        return 0, NOTE_AUDIO_FAIL if onset_times_ms is None else NOTE_TOO_FEW

    # ── Calculate per-line offsets using first N lines ─────────────
    # Use up to 10 lines to get a robust estimate
    num_lines_to_check = min(len(lines), 10)
    per_line_offsets: list[int] = []

    for i in range(num_lines_to_check):
        lrc_ms, _ = lines[i]
        nearest = _nearest_onset(lrc_ms, onset_times_ms, search_window_ms)
        if nearest is not None:
            offset = nearest - lrc_ms
            per_line_offsets.append(offset)

    if len(per_line_offsets) < min_consistent_lines:
        return 0, NOTE_NO_OFFSET

    # ── Check consistency: all offsets should be similar ───────────
    median_offset = _median(per_line_offsets)
    deviations = [abs(o - median_offset) for o in per_line_offsets]
    max_dev = max(deviations)

    if max_dev > max_deviation_ms:
        # Offsets are too inconsistent — not a global shift
        logger.debug(
            "global offset inconsistent: median %d ms, max deviation %d ms",
            median_offset, max_dev,
        )
        return 0, NOTE_NO_OFFSET

    # ── Check magnitude: offset must be significant ────────────────
    if abs(median_offset) < min_offset_ms:
        return 0, NOTE_NO_OFFSET

    # ── Apply the detected offset ──────────────────────────────────
    offset_ms = int(median_offset)
    note = f"{NOTE_OFFSET_APPLIED}: {offset_ms:+d} ms"
    logger.info(
        "%s (based on %d lines, max deviation %d ms)",
        note, len(per_line_offsets), max_dev,
    )
    return offset_ms, note


def _median(values: list[int]) -> int:
    """Return the median of a list of integers."""
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n % 2 == 1:
        return sorted_vals[n // 2]
    else:
        return (sorted_vals[n // 2 - 1] + sorted_vals[n // 2]) // 2


def apply_global_offset_to_bundle(
    bundle,
    audio_path: str,
    min_offset_ms: int = MIN_GLOBAL_OFFSET_MS,
) -> tuple[int, str]:
    """Detect and apply a global offset to a :class:`LyricsBundle` in-place.

    Convenience wrapper around :func:`detect_global_offset` that also
    mutates the bundle's lines and LRC string when an offset is found.

    Args:
        bundle: A :class:`LyricsBundle` with synced lyrics.
        audio_path: Path to the audio file.
        min_offset_ms: Minimum absolute offset to consider.

    Returns:
        Tuple of ``(applied_offset_ms, note)``.
    """
    if not bundle.has_sync or not bundle.lines:
        return 0, "sin letras sincronizadas"

    offset_ms, note = detect_global_offset(bundle.lines, audio_path, min_offset_ms)
    if offset_ms == 0:
        return 0, note

    # Apply the offset to all lines
    new_lines = _apply_offset(bundle.lines, offset_ms)
    new_lines.sort(key=lambda x: x[0])
    bundle.lines = new_lines
    bundle.lrc = normalise_lrc(new_lines)
    bundle.offset_ms += offset_ms
    bundle.warnings.append(f"offset global automático: {note}")

    return offset_ms, note


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
