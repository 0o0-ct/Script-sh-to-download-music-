#!/usr/bin/env python3
"""Music library report (library command).

Supports:
  - Basic report with stats on titles, artists, albums, covers, lyrics
  - JSON output with --json flag
  - Acoustic duplicate detection with --find-duplicates (via fingerprint)
  - Incomplete album detection with --albums
  - Missing covers detection with --missing-covers
  - Missing lyrics detection with --missing-lyrics
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    import mutagen
    from mutagen.mp3 import MP3
    from mutagen.flac import FLAC
    from mutagen.mp4 import MP4
    HAS_MUTAGEN = True
except ImportError:
    HAS_MUTAGEN = False


AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".ogg", ".wav", ".opus"}


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


def _find_acoustic_duplicates(infos: list[dict]) -> list[dict]:
    """Find acoustic duplicates using fingerprint comparison.

    Returns a list of duplicate groups, each containing files with
    similar acoustic fingerprints.
    """
    try:
        from otterrip_core.audio_fingerprint import compute_fingerprint
    except ImportError:
        print("  audio_fingerprint no disponible para duplicados acústicos.", file=sys.stderr)
        return []

    # Compute fingerprints
    fp_map: dict[str, list[dict]] = {}
    for info in infos:
        filepath = info.get("file", "")
        if not filepath:
            continue
        try:
            fp = compute_fingerprint(filepath)
            if fp:
                fp_map.setdefault(fp, []).append(info)
        except Exception:
            continue

    # Return groups with more than one file
    return [
        {"fingerprint": fp, "files": files}
        for fp, files in fp_map.items()
        if len(files) > 1
    ]


def _find_incomplete_albums(infos: list[dict]) -> list[dict]:
    """Find albums that may be incomplete (missing tracks).

    Groups files by album and checks if track numbers are sequential.
    """
    albums: dict[str, list[dict]] = {}
    for info in infos:
        album = info.get("album", "")
        if not album:
            continue
        albums.setdefault(album, []).append(info)

    incomplete = []
    for album, tracks in albums.items():
        if len(tracks) < 2:
            continue

        # Extract track numbers
        track_nums = []
        for t in tracks:
            track_str = t.get("track", "")
            if "/" in track_str:
                try:
                    total = int(track_str.split("/")[1])
                    track_nums.append(("total", total))
                except ValueError:
                    pass
            try:
                num = int(track_str.split("/")[0]) if "/" in track_str else int(track_str)
                track_nums.append(("num", num))
            except ValueError:
                continue

        # Check if we have total info from tags
        totals = [v for k, v in track_nums if k == "total"]
        nums = [v for k, v in track_nums if k == "num"]

        if totals and nums:
            expected = max(totals)
            if len(nums) < expected:
                incomplete.append({
                    "album": album,
                    "expected": expected,
                    "found": len(nums),
                    "missing": expected - len(nums),
                    "files": tracks,
                })

    return incomplete


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

    # Build report dict for JSON output
    report = {
        "path": str(target),
        "total_files": total,
        "with_title": with_title,
        "with_artist": with_artist,
        "with_album": with_album,
        "with_cover": with_cover,
        "with_lyrics": with_lyrics,
        "no_title": no_title,
        "no_artist": no_artist,
        "no_cover": no_cover,
        "no_lyrics": no_lyrics,
        "duplicate_groups": len(duplicates),
        "files": infos,
    }

    # JSON output
    if getattr(args, "json", False):
        # Add acoustic duplicates if requested
        if getattr(args, "find_duplicates", False):
            acoustic_dups = _find_acoustic_duplicates(infos)
            report["acoustic_duplicate_groups"] = len(acoustic_dups)
            report["acoustic_duplicates"] = acoustic_dups

        # Add incomplete albums if requested
        if getattr(args, "albums", False):
            incomplete = _find_incomplete_albums(infos)
            report["incomplete_albums"] = len(incomplete)
            report["incomplete_album_details"] = incomplete

        # Add missing covers
        if getattr(args, "missing_covers", False):
            missing_covers = [i for i in infos if not i.get("has_cover")]
            report["missing_covers"] = missing_covers

        # Add missing lyrics
        if getattr(args, "missing_lyrics", False):
            missing_lyrics = [i for i in infos if not i.get("has_lyrics")]
            report["missing_lyrics"] = missing_lyrics

        print(json.dumps(report, ensure_ascii=False, default=str))
        return 0

    # Text output
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

    # Acoustic duplicates
    if getattr(args, "find_duplicates", False):
        print()
        print("  Buscando duplicados acústicos...")
        acoustic_dups = _find_acoustic_duplicates(infos)
        if acoustic_dups:
            print(f"  ⚠ Duplicados acústicos: {len(acoustic_dups)} grupo(s)")
            for group in acoustic_dups[:10]:
                fp = group["fingerprint"]
                files = group["files"]
                print(f"    • Huella {fp}: {len(files)} archivos")
                for f in files[:3]:
                    title = f.get("title", f.get("name", "?"))
                    print(f"      - {title}")
                if len(files) > 3:
                    print(f"      ... y {len(files) - 3} más")
        else:
            print("  ✓ No se encontraron duplicados acústicos.")

    # Incomplete albums
    if getattr(args, "albums", False):
        print()
        print("  Buscando álbumes incompletos...")
        incomplete = _find_incomplete_albums(infos)
        if incomplete:
            print(f"  ⚠ Álbumes incompletos: {len(incomplete)}")
            for album in incomplete[:10]:
                print(f"    • {album['album']}: {album['found']}/{album['expected']} pistas")
        else:
            print("  ✓ Todos los álbumes parecen completos.")

    # Missing covers
    if getattr(args, "missing_covers", False):
        missing_covers = [i for i in infos if not i.get("has_cover")]
        if missing_covers:
            print()
            print(f"  ⚠ Sin portada: {len(missing_covers)} archivo(s)")
            for i in missing_covers[:10]:
                title = i.get("title", i.get("name", "?"))
                artist = i.get("artist", "")
                print(f"    • {artist} - {title}" if artist else f"    • {title}")
            if len(missing_covers) > 10:
                print(f"    ... y {len(missing_covers) - 10} más")

    # Missing lyrics
    if getattr(args, "missing_lyrics", False):
        missing_lyrics = [i for i in infos if not i.get("has_lyrics")]
        if missing_lyrics:
            print()
            print(f"  ⚠ Sin letra: {len(missing_lyrics)} archivo(s)")
            for i in missing_lyrics[:10]:
                title = i.get("title", i.get("name", "?"))
                artist = i.get("artist", "")
                print(f"    • {artist} - {title}" if artist else f"    • {title}")
            if len(missing_lyrics) > 10:
                print(f"    ... y {len(missing_lyrics) - 10} más")

    print()

    if getattr(args, "save", False):
        out = target / ".otterrip_library_report.json"
        out.write_text(json.dumps(infos, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  Informe guardado en: {out}")

    return 0
