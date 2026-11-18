#!/usr/bin/env python3
"""Volume normalization for audio files.

Provides two normalization modes:
1. **ReplayGain** — computes gain metadata tags without re-encoding.
   Uses ffmpeg's `ebur128` filter to measure loudness, then writes
   REPLAYGAIN_GAIN and REPLAYGAIN_PEAK tags via mutagen.
2. **EBU R128 loudnorm** — two-pass FFmpeg loudnorm filter that
   re-encodes the audio to match a target integrated loudness.

Usage:
    py_core normalize <target> [--mode replaygain|loudnorm] \
        [--target-lufs -16] [--true-peak -1.5]
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger("otterrip_core.normalize")

AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".ogg", ".wav", ".opus"}

# Default EBU R128 targets
DEFAULT_TARGET_LUFS = -16.0
DEFAULT_TRUE_PEAK = -1.5


def _find_audio_files(target: Path) -> list[Path]:
    """Find all audio files in *target* (file or directory)."""
    if target.is_file():
        if target.suffix.lower() in AUDIO_EXTS:
            return [target]
        return []
    if target.is_dir():
        return sorted(
            f for f in target.rglob("*")
            if f.is_file() and f.suffix.lower() in AUDIO_EXTS
        )
    return []


# ---------------------------------------------------------------------------
# ReplayGain via ffmpeg ebur128
# ---------------------------------------------------------------------------

def _measure_loudness_ffmpeg(filepath: Path) -> Optional[dict]:
    """Measure integrated loudness using ffmpeg ebur128 filter.

    Returns a dict with `target_change`, `true_peak`, `input_i`, `input_tp`,
    `input_num_samples`, or ``None`` on failure.
    """
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-nostats", "-hide_banner", "-i", str(filepath),
                "-filter_complex", "ebur128=peak=true",
                "-f", "null", "-",
            ],
            capture_output=True,
            timeout=120,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.warning("ffmpeg ebur128 failed for %s: %s", filepath, exc)
        return None

    text = result.stderr.decode("utf-8", errors="replace")

    # Parse the summary section at the end
    target_change = None
    true_peak = None
    input_i = None
    input_tp = None

    m = re.search(r"Target Offset\s*:\s*([+\-]?[\d.]+)\s*dB", text)
    if m:
        try:
            target_change = float(m.group(1))
        except ValueError:
            pass

    m = re.search(r"True peak\s*:\s*([-\d.]+)\s*dBFS", text)
    if m:
        try:
            true_peak = float(m.group(1))
        except ValueError:
            pass

    m = re.search(r"Integrated loudness\s*:\s*([-\d.]+)\s*LUFS", text)
    if m:
        try:
            input_i = float(m.group(1))
        except ValueError:
            pass

    m = re.search(r"True peak\s*:\s*([-\d.]+)\s*dBFS", text)
    if m:
        try:
            input_tp = float(m.group(1))
        except ValueError:
            pass

    return {
        "target_change": target_change,
        "true_peak": true_peak,
        "input_i": input_i,
        "input_tp": input_tp,
    }


def _write_replaygain_tags(filepath: Path, gain_db: float, peak: float) -> bool:
    """Write ReplayGain tags to *filepath* using mutagen.

    Args:
        filepath: Audio file path.
        gain_db: ReplayGain gain in dB (negative means louder).
        peak: True peak value (0.0-1.0 range for the tag).

    Returns:
        ``True`` if tags were written successfully.
    """
    try:
        import mutagen
        from mutagen.id3 import ID3, TXXX, APIC
        from mutagen.flac import FLAC, VorbisComment
        from mutagen.mp4 import MP4, MP4Cover
    except ImportError:
        logger.warning("mutagen not available for ReplayGain tags")
        return False

    try:
        ext = filepath.suffix.lower()
        audio = mutagen.File(str(filepath))
        if audio is None:
            return False

        # Normalize peak to 0.0-1.0 range from dBFS
        peak_normalized = 1.0
        if peak is not None and peak < 0:
            import math
            peak_normalized = 10 ** (peak / 20.0)
        peak_normalized = min(peak_normalized, 1.0)

        if ext == ".mp3":
            try:
                if not audio.tags:
                    audio.add_tags()
                audio.tags.add(
                    TXXX(encoding=3, desc="REPLAYGAIN_TRACK_GAIN",
                         value=f"{gain_db:+.2f} dB")
                )
                audio.tags.add(
                    TXXX(encoding=3, desc="REPLAYGAIN_TRACK_PEAK",
                         value=f"{peak_normalized:.8f}")
                )
            except Exception:
                return False
        elif ext == ".flac":
            if isinstance(audio, FLAC):
                audio["REPLAYGAIN_TRACK_GAIN"] = f"{gain_db:+.2f} dB"
                audio["REPLAYGAIN_TRACK_PEAK"] = f"{peak_normalized:.8f}"
        elif ext == ".m4a":
            # M4A uses different tag names
            try:
                if isinstance(audio, MP4):
                    # Store as raw bytes
                    audio["\xa9rgn"] = [f"{gain_db:+.2f} dB".encode()]
                    audio["\xa9rpk"] = [f"{peak_normalized:.8f}".encode()]
            except Exception:
                return False
        elif ext in (".ogg", ".opus"):
            # Vorbis comments
            try:
                audio["replaygain_track_gain"] = f"{gain_db:+.2f} dB"
                audio["replaygain_track_peak"] = f"{peak_normalized:.8f}"
            except Exception:
                return False
        else:
            return False

        audio.save()
        return True
    except Exception as exc:
        logger.warning("Failed to write ReplayGain tags for %s: %s", filepath, exc)
        return False


def compute_replaygain(filepath: Path) -> bool:
    """Compute and embed ReplayGain tags for a single file.

    Uses ffmpeg ebur128 to measure loudness, then writes REPLAYGAIN_GAIN
    and REPLAYGAIN_PEAK tags via mutagen.

    Args:
        filepath: Path to the audio file.

    Returns:
        ``True`` if tags were successfully written.
    """
    if not shutil.which("ffmpeg"):
        logger.error("ffmpeg not found; cannot compute ReplayGain")
        return False

    info = _measure_loudness_ffmpeg(filepath)
    if info is None or info.get("target_change") is None:
        logger.warning("Could not measure loudness for %s", filepath)
        return False

    gain_db = info["target_change"]
    peak = info.get("true_peak", -6.0)

    if _write_replaygain_tags(filepath, gain_db, peak):
        logger.info(
            "ReplayGain for %s: gain=%.2f dB, peak=%.2f dBFS",
            filepath.name, gain_db, peak,
        )
        return True
    return False


# ---------------------------------------------------------------------------
# EBU R128 loudnorm (two-pass)
# ---------------------------------------------------------------------------

def _loudnorm_pass1(filepath: Path, target_lufs: float, true_peak: float) -> Optional[dict]:
    """Run the first pass of ffmpeg loudnorm to measure stats.

    Returns a dict with the measured stats, or ``None`` on failure.
    """
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-i", str(filepath),
                "-filter_complex",
                f"loudnorm=I={target_lufs}:TP={true_peak}:LRA=11:print_format=json",
                "-f", "null", "-",
            ],
            capture_output=True,
            timeout=300,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        logger.warning("loudnorm pass 1 failed for %s: %s", filepath, exc)
        return None

    # The stats are in the last JSON block of stderr
    text = result.stderr.decode("utf-8", errors="replace")

    # Find the last JSON block in stderr
    lines = text.split("\n")
    # Find the last line containing "}"
    json_end = -1
    for i in range(len(lines) - 1, -1, -1):
        if "}" in lines[i]:
            json_end = i
            break
    if json_end < 0:
        return None
    # Find the matching "{" before json_end
    json_start = -1
    for i in range(json_end, -1, -1):
        if "{" in lines[i]:
            json_start = i
            break

    if json_start >= 0:
        json_text = "\n".join(lines[json_start:json_end + 1])
        try:
            return json.loads(json_text)
        except json.JSONDecodeError:
            pass

    return None


def apply_loudnorm(
    filepath: Path,
    target_lufs: float = DEFAULT_TARGET_LUFS,
    true_peak: float = DEFAULT_TRUE_PEAK,
) -> bool:
    """Apply EBU R128 loudnorm two-pass normalization.

    Re-encodes the audio file to match the target integrated loudness.

    Args:
        filepath: Path to the audio file.
        target_lufs: Target integrated loudness in LUFS (default -16).
        true_peak: True peak limit in dBTP (default -1.5).

    Returns:
        ``True`` if normalization succeeded.
    """
    if not shutil.which("ffmpeg"):
        logger.error("ffmpeg not found; cannot apply loudnorm")
        return False

    # Pass 1: measure
    stats = _loudnorm_pass1(filepath, target_lufs, true_peak)
    if stats is None:
        logger.warning("Could not measure loudnorm stats for %s", filepath)
        return False

    # Extract measured values
    measured_i = stats.get("input_i", str(target_lufs))
    measured_tp = stats.get("input_tp", str(true_peak))
    measured_lra = stats.get("input_lra", "11")
    measured_thresh = stats.get("input_thresh", "-2.0")
    offset = stats.get("target_offset", "0.00")

    # Pass 2: apply with measured values
    tmp_fd, tmp_path = tempfile.mkstemp(
        suffix=f"_{filepath.stem}.tmp",
        prefix=".loudnorm_",
        dir=str(filepath.parent),
    )
    os.close(tmp_fd)
    tmp_path = Path(tmp_path)

    try:
        cmd = [
            "ffmpeg", "-y", "-i", str(filepath),
            "-filter_complex",
            (
                f"loudnorm=I={target_lufs}:TP={true_peak}:LRA=11:"
                f"measured_I={measured_i}:measured_TP={measured_tp}:"
                f"measured_LRA={measured_lra}:measured_thresh={measured_thresh}:"
                f"offset={offset}:linear=true:print_format=summary"
            ),
            "-map_metadata", "0",
            "-map", "0",
            str(tmp_path),
        ]

        result = subprocess.run(cmd, capture_output=True, timeout=300)
        if result.returncode != 0:
            logger.warning(
                "loudnorm pass 2 failed for %s: %s",
                filepath, result.stderr.decode("utf-8", errors="replace")[:200],
            )
            tmp_path.unlink(missing_ok=True)
            return False

        # Replace original with normalized file
        tmp_path.replace(filepath)
        logger.info(
            "Loudnorm applied to %s: I=%.1f LUFS, TP=%.1f dBTP",
            filepath.name, target_lufs, true_peak,
        )
        return True
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("loudnorm pass 2 error for %s: %s", filepath, exc)
        tmp_path.unlink(missing_ok=True)
        return False


# ---------------------------------------------------------------------------
# CLI handler
# ---------------------------------------------------------------------------

def run_normalize(args) -> int:
    """CLI handler for the ``normalize`` subcommand.

    Args:
        args: Parsed arguments with ``target``, ``mode``, ``target_lufs``,
              and ``true_peak``.

    Returns:
        Exit code (0 = success).
    """
    target = Path(args.target)
    mode = getattr(args, "mode", "replaygain") or "replaygain"
    target_lufs = getattr(args, "target_lufs", DEFAULT_TARGET_LUFS)
    true_peak = getattr(args, "true_peak", DEFAULT_TRUE_PEAK)

    if not target.exists():
        print(f"  Ruta no encontrada: {args.target}")
        return 1

    files = _find_audio_files(target)
    if not files:
        print("  No se encontraron archivos de audio.")
        return 0

    print(f"  Normalizando {len(files)} archivo(s) en modo '{mode}'...")

    ok = 0
    failed = 0
    for fpath in files:
        try:
            if mode == "replaygain":
                success = compute_replaygain(fpath)
            elif mode == "loudnorm":
                success = apply_loudnorm(fpath, target_lufs, true_peak)
            else:
                print(f"  Modo desconocido: {mode} (usa 'replaygain' o 'loudnorm')")
                return 1

            if success:
                print(f"  ✔ {fpath.name}")
                ok += 1
            else:
                print(f"  ✘ {fpath.name}")
                failed += 1
        except Exception as exc:
            print(f"  ✘ {fpath.name}: {exc}")
            failed += 1

    print(f"  Normalizados: {ok}/{len(files)}")
    if failed:
        print(f"  Fallidos: {failed}")
    return 0
