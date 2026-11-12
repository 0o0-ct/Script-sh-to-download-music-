#!/usr/bin/env python3
"""Verify downloaded audio files (verify-session command)."""
from __future__ import annotations

import json
import os
from pathlib import Path

try:
    import mutagen
    from mutagen.mp3 import MP3
    from mutagen.flac import FLAC
    from mutagen.mp4 import MP4
    HAS_MUTAGEN = True
except ImportError:
    HAS_MUTAGEN = False


AUDIO_EXTS = {".mp3": "mp3", ".flac": "flac", ".m4a": "m4a", ".ogg": "ogg", ".wav": "wav"}


def _get_duration(filepath: Path) -> float | None:
    """Return duration in seconds, or None."""
    if not HAS_MUTAGEN:
        return None
    try:
        ext = filepath.suffix.lower()
        if ext == ".mp3":
            audio = MP3(str(filepath))
        elif ext == ".flac":
            audio = FLAC(str(filepath))
        elif ext == ".m4a":
            audio = MP4(str(filepath))
        else:
            audio = mutagen.File(str(filepath))
        if audio is not None and hasattr(audio, "info") and audio.info:
            return audio.info.length
    except Exception:
        pass
    return None


def _has_cover(filepath: Path) -> bool:
    """Check if the file has an embedded cover image."""
    if not HAS_MUTAGEN:
        return False
    try:
        ext = filepath.suffix.lower()
        if ext == ".mp3":
            audio = MP3(str(filepath))
            return bool(audio.tags and audio.tags.get("APIC:"))
        elif ext == ".flac":
            audio = FLAC(str(filepath))
            return bool(audio.tags and audio.tags.get("COVERART"))
        elif ext == ".m4a":
            audio = MP4(str(filepath))
            return bool(audio.tags and audio.tags.get("\xa9art") or audio.tags.get("covr"))
    except Exception:
        pass
    return False


def run_verify_session(args) -> int:
    """Verify files listed in a session and split into ok/failed."""
    session_file = Path(args.session)
    ext = args.ext
    dest = Path(args.dest)
    archive_path = Path(args.archive) if args.archive else None
    out_ok = Path(args.out_ok)
    out_failed = Path(args.out_failed)

    # Read archive set
    archived: set[str] = set()
    if archive_path and archive_path.is_file():
        try:
            archived = set(
                line.strip()
                for line in archive_path.read_text(encoding="utf-8", errors="replace").splitlines()
                if line.strip()
            )
        except OSError:
            pass

    # Read session — each line is a video ID
    session_ids: list[str] = []
    if session_file.is_file():
        try:
            session_ids = [
                line.strip()
                for line in session_file.read_text(encoding="utf-8", errors="replace").splitlines()
                if line.strip()
            ]
        except OSError:
            pass

    ok_files: list[Path] = []
    failed_files: list[str] = []

    # Find audio files in dest
    audio_files: list[Path] = []
    if dest.is_dir():
        for child in dest.rglob("*"):
            if child.is_file() and child.suffix.lower() in AUDIO_EXTS:
                audio_files.append(child)

    # Match session IDs to files
    for vid_id in session_ids:
        found = False
        for af in audio_files:
            if vid_id in af.name:
                dur = _get_duration(af)
                cover = _has_cover(af)
                if dur is not None and dur > 0:
                    info = {
                        "file": str(af),
                        "duration": round(dur, 2),
                        "cover_missing": not cover,
                    }
                    ok_files.append(af)
                    # Write JSON info to out_ok
                else:
                    failed_files.append(str(af))
                found = True
                break
        if not found:
            failed_files.append(vid_id)

    # Write outputs
    ok_lines = []
    for af in ok_files:
        dur = _get_duration(af)
        cover = _has_cover(af)
        info = {
            "file": str(af),
            "duration": round(dur, 2) if dur else 0,
            "cover_missing": not cover,
        }
        ok_lines.append(json.dumps(info))

    out_ok.write_text("\n".join(ok_lines) + ("\n" if ok_lines else ""), encoding="utf-8")
    out_failed.write_text("\n".join(failed_files) + ("\n" if failed_files else ""), encoding="utf-8")

    return 0
