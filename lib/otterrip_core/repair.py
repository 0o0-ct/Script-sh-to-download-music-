#!/usr/bin/env python3
"""Repair metadata (repair command)."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from otterrip_core.fix_metadata import (
    HAS_MUTAGEN,
    _get_audio_file,
    _get_tags,
    _set_tags,
    _clean_title,
)


def run_repair(args) -> int:
    """Repair metadata with dry-run or apply mode."""
    target = Path(args.target)
    apply_changes = args.apply
    clean_noise = args.clean_noise

    if not HAS_MUTAGEN:
        print("  mutagen no disponible.")
        return 1

    if not target.exists():
        print(f"Ruta no encontrada: {args.target}")
        return 1

    # Collect files
    audio_exts = {".mp3", ".flac", ".m4a"}
    if target.is_file():
        files = [target]
    else:
        files = sorted(
            f for f in target.rglob("*")
            if f.is_file() and f.suffix.lower() in audio_exts
        )

    if not files:
        print("No se encontraron archivos de audio.")
        return 0

    changes: list[tuple[Path, dict, dict]] = []

    for fpath in files:
        audio = _get_audio_file(str(fpath))
        if audio is None:
            continue
        old_tags = _get_tags(audio)
        new_tags = dict(old_tags)

        # Clean title noise
        title = new_tags.get("title", "")
        if title:
            cleaned = _clean_title(title)
            if clean_noise:
                # Additional noise cleaning
                for pat in [
                    re.compile(r"\s*\(.*?\)\s*$", re.I),
                    re.compile(r"\s*—\s*\d{4}\s*$"),
                ]:
                    cleaned = pat.sub("", cleaned)
                cleaned = cleaned.strip()
            if cleaned != title:
                new_tags["title"] = cleaned

        # Fix "Artist - Topic" in artist field
        artist = new_tags.get("artist", "")
        if artist and "- Topic" in artist:
            new_tags["artist"] = artist.replace("- Topic", "").strip()

        # Fix "Artist - Title" pattern in title
        if " - " in new_tags.get("title", "") and not new_tags.get("artist"):
            parts = new_tags["title"].split(" - ", 1)
            if len(parts) == 2:
                new_tags["artist"] = parts[0].strip()
                new_tags["title"] = parts[1].strip()

        if new_tags != old_tags:
            changes.append((fpath, old_tags, new_tags))

    if not changes:
        print("  No se detectaron cambios necesarios.")
        return 0

    if not apply_changes:
        # Dry run — show what would change
        print(f"  Se detectaron {len(changes)} archivo(s) con cambios posibles:")
        for fpath, old, new in changes[:20]:
            print(f"\n  {fpath.name}")
            for key in ["title", "artist", "album"]:
                ov = old.get(key, "")
                nv = new.get(key, "")
                if ov != nv:
                    print(f"    {key}:")
                    print(f"      → {ov}")
                    print(f"      → {nv}")
        if len(changes) > 20:
            print(f"  ... y {len(changes) - 20} más")
        return 0

    # Apply changes
    # Create backup directory
    state_dir = Path(__import__("os").environ.get("OTTERRIP_STATE_DIR", ""))
    backup_dir = state_dir / "backups" if state_dir else None
    if backup_dir:
        backup_dir.mkdir(parents=True, exist_ok=True)

    fixed = 0
    for fpath, old, new in changes:
        try:
            # Backup
            if backup_dir:
                backup_path = backup_dir / fpath.name
                if backup_path.exists():
                    backup_path = backup_dir / f"{fpath.stem}~{fixed}{fpath.suffix}"
                shutil.copy2(str(fpath), str(backup_path))

            audio = _get_audio_file(str(fpath))
            if audio:
                _set_tags(
                    audio,
                    new.get("title", ""),
                    new.get("artist", ""),
                    new.get("album", ""),
                    new.get("track", ""),
                    new.get("date", ""),
                    new.get("genre", ""),
                )
                audio.save()
                fixed += 1
        except Exception as exc:
            print(f"  Error al reparar {fpath.name}: {exc}")

    print(f"  Reparados: {fixed}/{len(changes)} archivo(s)")
    return 0
