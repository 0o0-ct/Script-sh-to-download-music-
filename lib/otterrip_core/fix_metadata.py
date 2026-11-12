#!/usr/bin/env python3
"""Fix audio metadata (fix-metadata command)."""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from datetime import datetime

try:
    import mutagen
    from mutagen.mp3 import MP3
    from mutagen.flac import FLAC
    from mutagen.mp4 import MP4, MP4Cover
    from mutagen.id3 import ID3, APIC, TIT2, TPE1, TALB, TDRC, TRCK, TCON, USLT, SYLT
    from mutagen.id3 import ID3NoHeaderError
    HAS_MUTAGEN = True
except ImportError:
    HAS_MUTAGEN = False


# Noise patterns to clean from titles
NOISE_PATTERNS = [
    re.compile(r"\s*\(Official\s+(Video|Audio|Music\s+Video)\)\s*", re.I),
    re.compile(r"\s*\[Lyrics?\]\s*", re.I),
    re.compile(r"\s*\[Official\s+\w+\]\s*", re.I),
    re.compile(r"\s*\(Lyrics?\)\s*", re.I),
    re.compile(r"\s*\(Lyric\s+Video\)\s*", re.I),
    re.compile(r"\s*—?\s*Topic\s*$", re.I),
]


def _clean_title(title: str) -> str:
    """Remove noise from title."""
    for pat in NOISE_PATTERNS:
        title = pat.sub("", title)
    return title.strip()


def _get_audio_file(filepath: str):
    """Open an audio file with mutagen."""
    if not HAS_MUTAGEN:
        return None
    try:
        ext = Path(filepath).suffix.lower()
        if ext == ".mp3":
            return MP3(filepath)
        elif ext == ".flac":
            return FLAC(filepath)
        elif ext == ".m4a":
            return MP4(filepath)
        return mutagen.File(filepath)
    except Exception:
        return None


def _read_session(session_path: str) -> list[str]:
    """Read verified session file (JSON lines with file paths)."""
    import json
    files = []
    p = Path(session_path)
    if not p.is_file():
        return files
    try:
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                info = json.loads(line)
                files.append(info.get("file", ""))
            except json.JSONDecodeError:
                files.append(line)
    except OSError:
        pass
    return files


def _set_tags(audio, title: str, artist: str, album: str, track: str, year: str, genre: str) -> None:
    """Set basic tags on an audio file."""
    if audio is None:
        return
    try:
        ext = ""
        if isinstance(audio, MP3):
            ext = "mp3"
            if audio.tags is None:
                audio.add_tags()
            audio.tags["TIT2"] = title
            audio.tags["TPE1"] = artist
            audio.tags["TALB"] = album
            audio.tags["TDRC"] = year
            audio.tags["TRCK"] = track
            if genre:
                audio.tags["TCON"] = genre
        elif isinstance(audio, FLAC):
            ext = "flac"
            if title:
                audio["title"] = title
            if artist:
                audio["artist"] = artist
            if album:
                audio["album"] = album
            if track:
                audio["tracknumber"] = track
            if year:
                audio["date"] = year
            if genre:
                audio["genre"] = genre
        elif isinstance(audio, MP4):
            ext = "m4a"
            if title:
                audio["\xa9nam"] = title
            if artist:
                audio["\xa9ART"] = artist
            if album:
                audio["\xa9alb"] = album
            if track:
                audio["trkn"] = [(int(track.split("/")[0]), 0)] if "/" in track else [int(track), 0]
            if year:
                audio["\xa9day"] = year
            if genre:
                audio["\xa9gen"] = genre
    except Exception:
        pass


def _set_cover(audio, cover_path: str) -> None:
    """Embed a cover image."""
    if audio is None or not cover_path or not Path(cover_path).is_file():
        return
    try:
        cover_data = Path(cover_path).read_bytes()
        if isinstance(audio, MP3):
            if audio.tags is None:
                audio.add_tags()
            audio.tags.delall("APIC:")
            audio.tags.add(
                APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover_data)
            )
        elif isinstance(audio, FLAC):
            audio.add_picture(cover_data)
        elif isinstance(audio, MP4):
            audio.tags["covr"] = [MP4Cover(cover_data, imageformat=MP4Cover.FORMAT_JPEG)]
    except Exception:
        pass


def _get_tags(audio) -> dict:
    """Extract tags from an audio file."""
    tags = {}
    if audio is None:
        return tags
    try:
        if isinstance(audio, MP3) and audio.tags:
            tags["title"] = str(audio.tags.get("TIT2", ""))
            tags["artist"] = str(audio.tags.get("TPE1", ""))
            tags["album"] = str(audio.tags.get("TALB", ""))
            tags["track"] = str(audio.tags.get("TRCK", ""))
            tags["date"] = str(audio.tags.get("TDRC", ""))
            tags["genre"] = str(audio.tags.get("TCON", ""))
        elif isinstance(audio, FLAC) and audio.tags:
            tags["title"] = audio.get("title", [""])[0]
            tags["artist"] = audio.get("artist", [""])[0]
            tags["album"] = audio.get("album", [""])[0]
            tags["track"] = audio.get("tracknumber", [""])[0]
            tags["date"] = audio.get("date", [""])[0]
            tags["genre"] = audio.get("genre", [""])[0]
        elif isinstance(audio, MP4) and audio.tags:
            tags["title"] = audio.get("\xa9nam", [""])[0]
            tags["artist"] = audio.get("\xa9ART", [""])[0]
            tags["album"] = audio.get("\xa9alb", [""])[0]
            tags["date"] = audio.get("\xa9day", [""])[0]
            tags["genre"] = audio.get("\xa9gen", [""])[0]
    except Exception:
        pass
    return tags


def run_fix_metadata(args) -> int:
    """Fix metadata for downloaded audio files."""
    target = args.target
    fmt = args.fmt
    session = args.session
    cover = args.cover

    if not HAS_MUTAGEN:
        print("  mutagen no disponible. Saltando reparación de metadatos.")
        return 0

    # Collect files
    files: list[str] = []
    if session:
        files = _read_session(session)
    elif Path(target).is_dir():
        for ext in [".mp3", ".flac", ".m4a"]:
            files.extend(str(f) for f in Path(target).rglob(f"*{ext}"))
    elif Path(target).is_file():
        files = [target]

    if not files:
        print("  No se encontraron archivos para reparar.")
        return 0

    fixed = 0
    for fpath in files:
        if not Path(fpath).is_file():
            continue
        audio = _get_audio_file(fpath)
        if audio is None:
            continue

        # Fix title noise
        tags = _get_tags(audio)
        title = tags.get("title", "")
        if title:
            cleaned = _clean_title(title)
            if cleaned != title:
                _set_tags(audio, cleaned, tags.get("artist", ""), tags.get("album", ""),
                          tags.get("track", ""), tags.get("date", ""), tags.get("genre", ""))
                audio.save()
                fixed += 1
                print(f"  Título limpiado: {fpath}")

        # Set cover
        if cover:
            _set_cover(audio, cover)
            audio.save()
            fixed += 1

    print(f"  Reparados: {fixed} archivo(s)")
    return 0
