#!/usr/bin/env python3
"""Automatic lyrics-to-audio alignment (offset estimation).

Synced lyrics from LRCLIB/Multi-Web are timed for a specific recording of
the song.  When the downloaded audio is a different edition (radio edit,
different intro, live version…), the whole lyric line is consistently
early or late — exactly the "la letra va adelantada/atrasada" symptom.

This module estimates ONE constant offset for the whole track by
cross-correlating the lyric line-start times against the audio's onset
envelope (via librosa, optional dependency) and, when the match is
confident, shifts the timestamps accordingly.

Sign convention matches :mod:`otterrip_core.offset`:

    * Positive offset  → timestamps move *later*  (lyrics were ahead)
    * Negative offset  → timestamps move *earlier* (lyrics were behind)

Confidence metric: for every candidate shift we measure how many lyric
lines land on a "hot" onset window (window mean ≥ ``HIT_RATIO`` × the
global envelope mean).  The confidence compares the best shift's hit
rate against the average hit rate over all shifts — a value near 1.0
means most lines only line up with onsets at *that* shift; a value near
0 means the best shift is indistinguishable from chance (noise).

librosa is an OPTIONAL dependency (requirements-align.txt).  Without it
the estimator reports zero confidence and nothing changes.
"""

from __future__ import annotations

import subprocess
from typing import Optional, Sequence

from otterrip_core.lrc_validator import normalise_lrc

# Sanity bounds for the automatic shift.
DEFAULT_MAX_SHIFT_MS = 15_000  # never shift more than ±15 s
MIN_USEFUL_SHIFT_MS = 200  # smaller offsets are jitter, not drift
MIN_LINES = 8  # need enough lines for statistics
CONFIDENCE_THRESHOLD = 0.55  # below this, do not touch the timestamps

# How much envelope we average around each (shifted) line start: vocals
# start on an onset, so a short positive window captures the entry.
ENTRY_WINDOW_MS = 600
# A line "hits" when its entry window averages this many times the
# global envelope mean.
HIT_RATIO = 2.0

# Stable note keys (aggregated by the caller; see lyrics._ALIGN_NOTE_COUNTS).
NOTE_NO_LIBROSA = "librosa no instalado — alineación automática omitida"
NOTE_AUDIO_FAIL = "no se pudo leer el audio para la alineación"
NOTE_NO_SIGNIFICANT = "sin desfase significativo"
NOTE_UNRELIABLE = "desfase poco fiable — no se aplicó"
NOTE_EXCEEDS_DURATION = "descartado: excedería la duración del audio"

try:  # pragma: no cover - exercised only when librosa is installed
    import librosa  # type: ignore

    HAS_LIBROSA = True
except Exception:  # ImportError or broken native deps
    HAS_LIBROSA = False


def _stats(values: Sequence[float]) -> tuple[float, float]:
    n = len(values)
    if not n:
        return 0.0, 0.0
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / n
    return mean, var**0.5


def _decode_audio_ffmpeg(path: str, sr: int = 22050) -> Optional["object"]:  # noqa: F821 - numpy array, only with librosa
    """Decode any audio format (m4a/aac/opus…) to mono float32 via ffmpeg.

    libsndfile (soundfile) — librosa's default reader — cannot read AAC/M4A,
    which is OtterRip's main output format.  ffmpeg is a hard requirement of
    the script, so decode straight to raw PCM on stdout.  ``sr`` 22050 Hz is
    plenty for onset detection and keeps memory small.
    """
    cmd = [
        "ffmpeg",
        "-v",
        "error",
        "-i",
        path,
        "-vn",
        "-map_metadata",
        "-1",
        "-f",
        "f32le",
        "-acodec",
        "pcm_f32le",
        "-ac",
        "1",
        "-ar",
        str(sr),
        "pipe:1",
    ]
    try:
        import numpy as np  # librosa's hard dependency; present whenever we run

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


def onset_envelope(path: str) -> Optional[tuple[list[float], float]]:
    """Load the audio and compute its onset-strength envelope.

    Returns ``(envelope, frame_ms)`` where *envelope* is a plain list of
    floats and *frame_ms* is the millisecond resolution of each frame.
    ``None`` when librosa is unavailable or the audio cannot be read.
    """
    if not HAS_LIBROSA:
        return None
    try:
        import numpy as np
    except Exception:
        # librosa presente pero numpy roto: sin datos no hay alineación.
        return None

    y = None
    sr = None
    try:
        y, sr = librosa.load(path, sr=None, mono=True)
    except Exception:
        y, sr = None, None
    if y is None or getattr(y, "size", 0) == 0:
        # soundfile/libsndfile cannot read this format (m4a/aac, opus…):
        # decode with ffmpeg instead.
        y = _decode_audio_ffmpeg(path)
        sr = 22050
        if y is None:
            return None
    try:
        env = librosa.onset.onset_strength(y=np.asarray(y, dtype=np.float32), sr=sr, hop_length=512)
        frame_ms = 512 / float(sr) * 1000.0
        return [float(v) for v in env], frame_ms
    except Exception:
        return None


def _line_scores(
    smooth: Sequence[float],
    prefix: Sequence[float],
    frame_ms: float,
    starts_ms: Sequence[int],
    win_frames: int,
    baseline: float,
    max_shift_ms: int,
) -> tuple[list[float], list[int], list[int]]:
    """Per-shift (score, hit_count) for every candidate shift.

    ``score`` is the mean entry-window ratio (window mean / baseline)
    across lines; ``hit_count`` counts lines whose ratio ≥ ``HIT_RATIO``.
    Returns parallel lists ``(scores, hits, shifts_ms)`` sorted from the
    most negative to the most positive shift.
    """
    n_frames = len(smooth)
    step = max(1, int(round(max(40.0, frame_ms) / frame_ms)))
    span_frames = max(1, int(max_shift_ms / frame_ms))
    scores: list[float] = []
    hits: list[int] = []
    shifts: list[int] = []
    for s_frames in range(-span_frames, span_frames + 1, step):
        shift_ms = int(round(s_frames * frame_ms))
        total = 0.0
        counted = 0
        hit = 0
        for start in starts_ms:
            idx = int((start + shift_ms) / frame_ms)
            # Window centred on the (shifted) line start: ±half window.
            half = win_frames // 2
            lo = max(0, idx - half)
            hi = min(n_frames, idx + (win_frames - half))
            if hi <= lo:
                continue
            mean = (prefix[hi] - prefix[lo]) / (hi - lo)
            ratio = mean / baseline
            total += ratio
            if ratio >= HIT_RATIO:
                hit += 1
            counted += 1
        if counted:
            scores.append(total / counted)
            hits.append(hit)
            shifts.append(shift_ms)
    return scores, hits, shifts


def estimate_offset_ms(
    envelope: Sequence[float],
    frame_ms: float,
    line_starts_ms: Sequence[int],
    max_shift_ms: int = DEFAULT_MAX_SHIFT_MS,
    min_lines: int = MIN_LINES,
) -> tuple[int, float]:
    """Estimate the constant offset aligning line starts with onsets.

    Returns ``(offset_ms, confidence)``.  ``confidence`` is 0.0–1.0 and 0.0
    when the input is too small to judge (e.g. fewer than ``min_lines``).
    """
    starts = sorted(int(s) for s in line_starts_ms)
    if len(starts) < min_lines or frame_ms <= 0 or len(envelope) < 10:
        return 0, 0.0

    # Light 3-frame smoothing to suppress single-frame spikes without
    # blurring the onset peaks (the 600 ms entry window already averages).
    smooth = _smooth3(envelope)
    n_frames = len(smooth)
    baseline, _ = _stats(smooth)
    if baseline <= 0:
        return 0, 0.0

    prefix = [0.0]
    for v in smooth:
        prefix.append(prefix[-1] + v)

    win_frames = max(1, int(ENTRY_WINDOW_MS / frame_ms))
    scores, hits, shifts = _line_scores(
        smooth,
        prefix,
        frame_ms,
        starts,
        win_frames,
        baseline,
        max_shift_ms,
    )
    if not scores:
        return 0, 0.0

    # Strict argmax; ties (identical scores, e.g. perfectly periodic music
    # where the same alignment repeats every beat period) break in favour
    # of the smallest |shift|.  Near-ties never steal the win: the peak is
    # real signal, and a ±40 ms error from frame jitter is acceptable.
    best_score = max(scores)
    eps = 1e-9 * max(1.0, best_score)
    best_idx = min(
        (i for i, s in enumerate(scores) if s >= best_score - eps),
        key=lambda i: abs(shifts[i]),
    )
    best_shift = shifts[best_idx]

    # Hit-rate confidence: fraction of lines that land on a hot window at
    # the best shift, compared to the average hit rate across all shifts.
    n_lines = len(starts)
    best_rate = hits[best_idx] / n_lines
    mean_rate = (sum(hits) / n_lines) / len(hits)
    if mean_rate >= 0.999:
        # Every shift is "hot" (saturated envelope): no information.
        return 0, 0.0
    confidence = (best_rate - mean_rate) / (1.0 - mean_rate)
    confidence = max(0.0, min(1.0, confidence))
    # A peak pinned to the search boundary is more likely spurious.
    if best_shift <= -max_shift_ms or best_shift >= max_shift_ms:
        confidence *= 0.5
    return best_shift, confidence


def _smooth3(values: Sequence[float]) -> list[float]:
    """Three-point moving average (ends are kept as-is)."""
    if len(values) < 3:
        return list(values)
    out = list(values)
    for i in range(1, len(values) - 1):
        out[i] = (values[i - 1] + values[i] + values[i + 1]) / 3.0
    return out


def shift_entries(
    lines: Sequence[tuple[int, str]],
    offset_ms: int,
    audio_duration_ms: Optional[int] = None,
) -> Optional[list[tuple[int, str]]]:
    """Shift ``(ms, text)`` entries, or ``None`` if the result is invalid.

    Rejects the shift when the last timestamp would land beyond the audio
    duration (+3 s tolerance), mirroring the LRC validator rule.
    """
    if not lines:
        return None
    shifted = [(max(0, ms + offset_ms), txt) for ms, txt in lines]
    shifted.sort(key=lambda x: x[0])
    if audio_duration_ms is not None and shifted[-1][0] > audio_duration_ms + 3000:
        return None
    return shifted


def auto_align_bundle(
    bundle,
    audio_path: str,
    audio_duration_s: Optional[float] = None,
    max_shift_ms: int = DEFAULT_MAX_SHIFT_MS,
    min_confidence: float = CONFIDENCE_THRESHOLD,
    min_lines: int = MIN_LINES,
) -> tuple[int, float, str]:
    """Align ``bundle`` (a :class:`LyricsBundle`) to the real audio.

    Returns ``(applied_offset_ms, confidence, note)``.  The bundle is
    mutated only when a confident shift is applied.  ``note`` explains
    why nothing happened; it is empty on success.
    """
    if not bundle.has_sync or not bundle.lines:
        return 0, 0.0, ""
    result = onset_envelope(audio_path)
    if result is None:
        if not HAS_LIBROSA:
            return 0, 0.0, NOTE_NO_LIBROSA
        return 0, 0.0, NOTE_AUDIO_FAIL
    envelope, frame_ms = result

    offset_ms, confidence = estimate_offset_ms(
        envelope,
        frame_ms,
        [ms for ms, _ in bundle.lines],
        max_shift_ms=max_shift_ms,
        min_lines=min_lines,
    )
    if abs(offset_ms) < MIN_USEFUL_SHIFT_MS:
        return 0, confidence, NOTE_NO_SIGNIFICANT
    if confidence < min_confidence:
        return 0, confidence, NOTE_UNRELIABLE

    duration_ms: Optional[int] = None
    if audio_duration_s is not None:
        duration_ms = int(round(audio_duration_s * 1000))
    elif bundle.audio_duration_ms is not None:
        duration_ms = bundle.audio_duration_ms

    new_lines = shift_entries(bundle.lines, offset_ms, duration_ms)
    if new_lines is None:
        return 0, confidence, NOTE_EXCEEDS_DURATION

    bundle.lines = new_lines
    bundle.lrc = normalise_lrc(new_lines)
    bundle.offset_ms += offset_ms
    return int(offset_ms), confidence, ""
