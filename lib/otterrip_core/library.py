#!/usr/bin/env python3
"""Music library report (library command)."""
from __future__ import annotations

from pathlib import Path

try:
    import mutagen
    from mutagen.mp3 import MP3
    from mutagen.flac import FLAC
    from mutagen.mp4 import MP4
    HAS_MUTAGEN = True
except ImportError:
    HAS_MUTAGEN = False


AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".ogg", ".wav"}


def _get_info(filepath: Path) -> dict:
    info: dict = {"file": str(filepath), "name": filepath.name}
    if not HAS_MUTAGEN:
        return info
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
        if audio is None:
            return info
        if hasattr(audio, "info") and audio.info:
            info["duration"] = round(audio.info.length, 1)
        # Tags
        if isinstance(audio, MP3) and audio.tags:
            info["title"] = str(audio.tags.get("TIT2", ""))
            info["artist"] = str(audio.tags.get("TPE1", ""))
            info["album"] = str(audio.tags.get("TALB", ""))
            info["has_cover"] = bool(audio.tags.get("APIC:"))
            info["has_lyrics"] = bool(audio.tags.get("USLT:"))
        elif isinstance(audio, FLAC) and audio.tags:
            info["title"] = audio.get("title", [""])[0]
            info["artist"] = audio.get("artist", [""])[0]
            info["album"] = audio.get("album", [""])[0]
            info["has_cover"] = bool(audio.get("COVERART"))
            info["has_lyrics"] = bool(audio.get("LYRICS"))
        elif isinstance(audio, MP4) and audio.tags:
            info["title"] = audio.get("\xa9nam", [""])[0]
            info["artist"] = audio.get("\xa9ART", [""])[0]
            info["album"] = audio.get("\xa9alb", [""])[0]
            info["has_cover"] = bool(audio.tags.get("covr"))
            info["has_lyrics"] = bool(audio.tags.get("\xa9lyr"))
    except Exception:
        pass
    return info


def run_library(args) -> int:
    """Generate a library report."""
    cmd = getattr(args, "library_cmd", None)
    if cmd != "report":
        print("Usage: library report <path>")
        return 1

    target = Path(args.path)
    if not target.exists():
        print(f"Ruta no encontrada: {args.path}")
        return 1

    files = sorted(
        f for f in target.rglob("*")
        if f.is_file() and f.suffix.lower() in AUDIO_EXTS
    )

    if not files:
        print("No se encontraron archivos de audio.")
        return 0

    infos = [_get_info(f) for f in files]

    # Stats
    total = len(infos)
    with_title = sum(1 for i in infos if i.get("title"))
    with_artist = sum(1 for i in infos if i.get("artist"))
    with_album = sum(1 for i in infos if i.get("album"))
    with_cover = sum(1 for i in infos if i.get("has_cover"))
    with_lyrics = sum(1 for i in infos if i.get("has_lyrics"))
    no_title = total - with_title
    no_artist = total - with_artist
    no_cover = total - with_cover
    no_lyrics = total - with_lyrics

    # Duplicates by title+artist
    seen: dict[str, list] = {}
    for info in infos:
        key = f"{info.get('artist', '?')} - {info.get('title', '?')}"
        seen.setdefault(key, []).append(info)
    duplicates = {k: v for k, v in seen.items() if len(v) > 1}

    print()
    print(f"  ── Informe de biblioteca: {target} ──")
    print()
    print(f"  Total archivos:        {total}")
    print(f"  Con título:            {with_title}")
    print(f"  Con artista:           {with_artist}")
    print(f"  Con álbum:             {with_album}")
    print(f"  Con portada:           {with_cover}")
    print(f"  Con letra:             {with_lyrics}")
    print()
    if no_title:
        print(f"  ⚠ Sin título:          {no_title}")
    if no_artist:
        print(f"  ⚠ Sin artista:         {no_artist}")
    if no_cover:
        print(f"  ⚠ Sin portada:         {no_cover}")
    if no_lyrics:
        print(f"  ⚠ Sin letra:           {no_lyrics}")
    if duplicates:
        print(f"  ⚠ Posibles duplicados: {len(duplicates)} grupo(s)")
        for key, dupes in list(duplicates.items())[:10]:
            print(f"    • {key} ({len(dupes)} archivos)")

    print()

    if getattr(args, "save", False):
        import json
        out = target / ".otterrip_library_report.json"
        out.write_text(json.dumps(infos, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  Informe guardado en: {out}")

    return 0
