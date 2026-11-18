#!/usr/bin/env python3
"""Music organizer for OtterRip.

Reorganize audio files using customizable templates like:
    {artist}/{album}/{track} - {title}

Supports dry-run mode to preview changes before applying.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path
from typing import Optional

try:
    import mutagen
    from mutagen.mp3 import MP3
    from mutagen.flac import FLAC
    from mutagen.mp4 import MP4
    HAS_MUTAGEN = True
except ImportError:
    HAS_MUTAGEN = False

AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav"}

# Default template
DEFAULT_TEMPLATE = "{artist}/{album}/{track} - {title}"


def _read_tags(filepath: Path) -> dict:
    """Extract tags from an audio file using mutagen."""
    tags = {
        "file": str(filepath),
        "name": filepath.name,
        "stem": filepath.stem,
        "ext": filepath.suffix,
    }
    if not HAS_MUTAGEN:
        return tags
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
            return tags
        if isinstance(audio, MP3) and audio.tags:
            tags["title"] = str(audio.tags.get("TIT2", ""))
            tags["artist"] = str(audio.tags.get("TPE1", ""))
            tags["album"] = str(audio.tags.get("TALB", ""))
            tags["track"] = str(audio.tags.get("TRCK", ""))
            tags["year"] = str(audio.tags.get("TDRC", ""))
            tags["genre"] = str(audio.tags.get("TCON", ""))
        elif isinstance(audio, FLAC) and audio.tags:
            tags["title"] = audio.get("title", [""])[0]
            tags["artist"] = audio.get("artist", [""])[0]
            tags["album"] = audio.get("album", [""])[0]
            tags["track"] = audio.get("tracknumber", [""])[0]
            tags["year"] = audio.get("date", [""])[0]
            tags["genre"] = audio.get("genre", [""])[0]
        elif isinstance(audio, MP4) and audio.tags:
            tags["title"] = audio.get("\xa9nam", [""])[0]
            tags["artist"] = audio.get("\xa9ART", [""])[0]
            tags["album"] = audio.get("\xa9alb", [""])[0]
            tags["year"] = audio.get("\xa9day", [""])[0]
            tags["genre"] = audio.get("\xa9gen", [""])[0]
    except Exception:
        pass
    return tags


def _sanitize_path_component(component: str) -> str:
    """Sanitize a path component to be filesystem-safe."""
    # Remove dangerous characters
    component = re.sub(r'[<>:"/\\|?*]', "", component)
    # Replace multiple spaces with single space
    component = re.sub(r"\s+", " ", component)
    # Strip whitespace
    component = component.strip()
    # Default if empty
    if not component:
        return "Unknown"
    return component


def _format_track_number(track: str) -> str:
    """Format track number with leading zero."""
    # Handle "3/12" format
    if "/" in track:
        num = track.split("/")[0]
    else:
        num = track
    try:
        return str(int(num)).zfill(2)
    except ValueError:
        return "00"


class Organizer:
    """Reorganize audio files using templates."""

    def __init__(self, template: str = DEFAULT_TEMPLATE):
        """Initialize with a template string.

        Args:
            template: Template string like "{artist}/{album}/{track} - {title}"
        """
        self.template = template

    def resolve_template(self, filepath: str) -> str:
        """Resolve a template for a given audio file.

        Args:
            filepath: Path to the audio file.

        Returns:
            Resolved relative path string.
        """
        tags = _read_tags(Path(filepath))

        # Build substitution dict
        subs = {
            "title": _sanitize_path_component(tags.get("title", tags.get("stem", "Unknown"))),
            "artist": _sanitize_path_component(tags.get("artist", "Unknown")),
            "album": _sanitize_path_component(tags.get("album", "Unknown")),
            "track": _format_track_number(tags.get("track", "00")),
            "year": _sanitize_path_component(tags.get("year", "")),
            "genre": _sanitize_path_component(tags.get("genre", "")),
            "ext": tags.get("ext", ".mp3"),
        }

        # Replace empty values with defaults
        if not subs["year"]:
            subs["year"] = "Unknown"
        if not subs["genre"]:
            subs["genre"] = "Unknown"

        # Apply template
        try:
            result = self.template.format(**subs)
        except KeyError as exc:
            print(f"  Error en plantilla: campo {exc} no reconocido", file=sys.stderr)
            return f"Unknown/{tags.get('stem', 'Unknown')}{tags.get('ext', '.mp3')}"

        # Ensure file extension is preserved
        ext = subs["ext"]
        if not result.lower().endswith(ext.lower()):
            result = f"{result}{ext}"

        return result

    def organize(self, source_dir: str, dry_run: bool = True) -> dict:
        """Reorganize files in a directory according to the template.

        Args:
            source_dir: Root directory containing audio files.
            dry_run: If True, only show what would be done.

        Returns:
            Dict with 'moved', 'skipped', 'errors' lists.
        """
        source = Path(source_dir)
        if not source.is_dir():
            print(f"Directorio no encontrado: {source_dir}", file=sys.stderr)
            return {"moved": [], "skipped": [], "errors": []}

        files = sorted(
            f for f in source.rglob("*")
            if f.is_file() and f.suffix.lower() in AUDIO_EXTS
        )

        moved = []
        skipped = []
        errors = []

        # Track destination paths to handle collisions
        dest_paths: dict[str, int] = {}

        for filepath in files:
            try:
                rel_path = self.resolve_template(str(filepath))
                dest = source / rel_path

                # Handle collisions
                dest_str = str(dest)
                if dest_str in dest_paths:
                    dest_paths[dest_str] += 1
                    suffix = f"_{dest_paths[dest_str]}"
                    stem = dest.with_name("")
                    name = dest.name
                    name_no_ext, ext = os.path.splitext(name)
                    dest = stem / f"{name_no_ext}{suffix}{ext}"
                    dest_str = str(dest)

                dest_paths[dest_str] = dest_paths.get(dest_str, 0)

                # Skip if already in correct location
                if filepath.resolve() == dest.resolve():
                    skipped.append({
                        "file": str(filepath),
                        "reason": "already_in_place",
                    })
                    continue

                # Create parent directory
                dest.parent.mkdir(parents=True, exist_ok=True)

                if dry_run:
                    moved.append({
                        "from": str(filepath),
                        "to": str(dest),
                        "dry_run": True,
                    })
                else:
                    shutil.move(str(filepath), str(dest))
                    moved.append({
                        "from": str(filepath),
                        "to": str(dest),
                        "dry_run": False,
                    })

                    # Clean up empty parent directories
                    parent = filepath.parent
                    while parent != source:
                        try:
                            if not any(parent.iterdir()):
                                parent.rmdir()
                                parent = parent.parent
                            else:
                                break
                        except OSError:
                            break

            except Exception as exc:
                errors.append({
                    "file": str(filepath),
                    "error": str(exc),
                })

        return {
            "moved": moved,
            "skipped": skipped,
            "errors": errors,
        }


def run_organize(args) -> int:
    """CLI handler for organize subcommand."""
    import json

    target = getattr(args, "path", "")
    if not target:
        print("Error: se requiere una ruta", file=sys.stderr)
        return 1

    template = getattr(args, "template", DEFAULT_TEMPLATE) or DEFAULT_TEMPLATE
    dry_run = getattr(args, "dry_run", True)

    organizer = Organizer(template)
    result = organizer.organize(target, dry_run=dry_run)

    moved_count = len(result["moved"])
    skipped_count = len(result["skipped"])
    error_count = len(result["errors"])

    mode = "SIMULACIÓN" if dry_run else "APLICADO"
    print()
    print(f"  ── Organización de archivos [{mode}] ──")
    print(f"  Plantilla: {template}")
    print()

    if moved_count > 0:
        print(f"  Archivos a {'mover' if dry_run else 'movidos'}: {moved_count}")
        for item in result["moved"][:30]:
            print(f"    {item['from']}")
            print(f"      → {item['to']}")
        if moved_count > 30:
            print(f"    ... y {moved_count - 30} más")

    if skipped_count > 0:
        print(f"  Sin cambios: {skipped_count}")

    if error_count > 0:
        print(f"  Errores: {error_count}")
        for item in result["errors"][:10]:
            print(f"    • {item['file']}: {item['error']}")

    print()

    if not dry_run and moved_count > 0:
        # Clean up empty directories
        source = Path(target)
        _cleanup_empty_dirs(source)

    if getattr(args, "json", False):
        print(json.dumps(result, ensure_ascii=False, default=str))

    return 0 if error_count == 0 else 1


def _cleanup_empty_dirs(source: Path) -> None:
    """Remove empty directories bottom-up."""
    for dirpath, dirnames, filenames in os.walk(str(source), topdown=False):
        dp = Path(dirpath)
        if dp == source:
            continue
        try:
            if not any(dp.iterdir()):
                dp.rmdir()
        except OSError:
            pass
