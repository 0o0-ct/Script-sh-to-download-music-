#!/usr/bin/env python3
"""Post-processing pipeline for downloaded audio files.

Orchestrates a complete metadata enrichment pipeline:
1. MusicBrainz lookup for canonical metadata.
2. Fix metadata tags with corrected title, artist, album.
3. Cover art from Cover Art Archive (or keep video thumbnail).
4. Lyrics embedding.
5. Final verification.

Usage:
    py_core post-process <target> [--keep-thumbnail] [--session]
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger("otterrip_core.post_process")

AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".ogg", ".wav", ".opus"}


def _find_audio_files(target: Path) -> list[Path]:
    """Find all audio files in *target* (file or directory)."""
    if target.is_file():
        if target.suffix.lower() in AUDIO_EXTS:
            return [target]
        return []
    if target.is_dir():
        return sorted(f for f in target.rglob("*") if f.is_file() and f.suffix.lower() in AUDIO_EXTS)
    return []


def _read_session_files(session_path: str) -> list[Path]:
    """Read file paths from a session manifest (JSON lines with "file" key)."""
    files: list[Path] = []
    p = Path(session_path)
    if not p.is_file():
        return files
    try:
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                fp = obj.get("file", "")
                if fp and Path(fp).is_file():
                    files.append(Path(fp))
            except json.JSONDecodeError:
                # Plain file path
                if Path(line).is_file():
                    files.append(Path(line))
    except OSError:
        pass
    return files


def _get_tags(filepath: Path) -> dict:
    """Read existing tags from an audio file using mutagen."""
    try:
        import mutagen

        audio = mutagen.File(str(filepath))
        if audio is None:
            return {}
        tags: dict = {}
        if hasattr(audio, "tags") and audio.tags:
            for key, value in audio.tags:
                if hasattr(value, "text") and value.text:
                    tags[str(key)] = str(value.text[0]) if value.text else ""
                else:
                    tags[str(key)] = str(value)
        return tags
    except Exception:
        return {}


def post_process_single(
    filepath: Path,
    keep_thumbnail: bool = False,
    lyrics: bool = True,
) -> dict:
    """Run the full post-processing pipeline on a single file.

    Steps:
    1. Read existing tags (title, artist, album).
    2. Lookup canonical metadata from MusicBrainz.
    3. Fix metadata tags.
    4. Fetch cover art from Cover Art Archive (unless keep_thumbnail).
    5. Embed lyrics (if enabled).
    6. Verify the result.

    Args:
        filepath: Path to the audio file.
        keep_thumbnail: If True, skip Cover Art Archive lookup.
        lyrics: If True, attempt to embed lyrics.

    Returns:
        A dict with status information about each step.
    """
    result = {
        "file": str(filepath),
        "mb_lookup": False,
        "metadata_fixed": False,
        "cover_art": False,
        "lyrics": False,
        "errors": [],
    }

    # Step 1: Read existing tags
    tags = _get_tags(filepath)
    title = tags.get("title", tags.get("TIT2", ""))
    artist = tags.get("artist", tags.get("TPE1", ""))
    album = tags.get("album", tags.get("TALB", ""))

    if not title:
        # Fallback: extract from filename
        stem = filepath.stem
        parts = stem.split(" - ", 1)
        if len(parts) == 2:
            artist, title = parts
        else:
            title = stem

    result["title"] = title.strip()
    result["artist"] = artist.strip()
    result["album"] = album.strip() if album else ""

    # Step 2: MusicBrainz lookup
    mb_meta = None
    if title.strip() and artist.strip():
        try:
            from otterrip_core.musicbrainz import lookup_canonical_sync

            mb_meta = lookup_canonical_sync(title.strip(), artist.strip(), album.strip() if album else "")
            if mb_meta:
                result["mb_lookup"] = True
                result["mb_title"] = mb_meta.title
                result["mb_artist"] = mb_meta.artist
                result["mb_album"] = mb_meta.album
                result["mb_release_id"] = mb_meta.release_id
                result["mb_track_number"] = mb_meta.track_number
        except Exception as exc:
            result["errors"].append(f"MusicBrainz lookup failed: {exc}")

    # Step 3: Fix metadata
    try:
        from otterrip_core import fix_metadata

        use_title = mb_meta.title if mb_meta and mb_meta.title else title.strip()
        use_artist = mb_meta.artist if mb_meta and mb_meta.artist else artist.strip()
        use_album = mb_meta.album if mb_meta and mb_meta.album else (album.strip() if album else "")

        fix_metadata._set_tags(
            filepath,
            title=use_title,
            artist=use_artist,
            album=use_album,
            track_number=mb_meta.track_number if mb_meta and mb_meta.track_number else 0,
        )
        result["metadata_fixed"] = True
    except Exception as exc:
        result["errors"].append(f"Fix metadata failed: {exc}")

    # Step 4: Cover art from Cover Art Archive
    if not keep_thumbnail and mb_meta and mb_meta.release_id:
        try:
            from otterrip_core import cover_art

            cover_data = cover_art.fetch_and_cache_cover(mb_meta.release_id, size="250")
            if cover_data:
                cover_art.embed_cover(filepath, cover_data)
                result["cover_art"] = True
        except Exception as exc:
            result["errors"].append(f"Cover art failed: {exc}")
    elif keep_thumbnail:
        result["cover_art"] = "kept_thumbnail"

    # Step 5: Lyrics (if enabled)
    if lyrics:
        try:
            from otterrip_core import lyrics as lyrics_module

            search_title = mb_meta.title if mb_meta and mb_meta.title else title.strip()
            search_artist = mb_meta.artist if mb_meta and mb_meta.artist else artist.strip()
            duration = None
            try:
                import mutagen

                audio = mutagen.File(str(filepath))
                if audio and hasattr(audio, "info") and audio.info:
                    duration = audio.info.length
            except Exception:
                pass

            bundle = lyrics_module.search_lyrics(
                artist=search_artist,
                title=search_title,
                duration=duration,
            )
            if bundle and lyrics_module.embed_lyrics(filepath, bundle):
                result["lyrics"] = True
        except Exception as exc:
            result["errors"].append(f"Lyrics failed: {exc}")

    return result


def run_post_process(args) -> int:
    """CLI handler for the ``post-process`` subcommand.

    Args:
        args: Parsed arguments with ``target``, ``keep_thumbnail``, and
              ``session``.

    Returns:
        Exit code (0 = success).
    """
    target = Path(getattr(args, "target", ""))
    keep_thumbnail = getattr(args, "keep_thumbnail", False)
    session = getattr(args, "session", "") or ""

    if not target.exists():
        print(f"  Ruta no encontrada: {args.target}")
        return 1

    # Collect files
    if session:
        files = _read_session_files(session)
    else:
        files = _find_audio_files(target)

    if not files:
        print("  No se encontraron archivos de audio.")
        return 0

    print(f"  Pipeline de etiquetado para {len(files)} archivo(s)...")

    ok = 0
    failed = 0
    for fpath in files:
        try:
            result = post_process_single(fpath, keep_thumbnail=keep_thumbnail)
            if result["metadata_fixed"]:
                print(f"  ✔ {fpath.name}: metadata {'✓' if result['metadata_fixed'] else '✘'}", end="")
                if result["mb_lookup"]:
                    print(" MB✓", end="")
                if result["cover_art"]:
                    print(" cover✓", end="")
                if result["lyrics"]:
                    print(" lyrics✓", end="")
                if result["errors"]:
                    print(f" ({len(result['errors'])} warnings)", end="")
                print()
                ok += 1
            else:
                print(f"  ✘ {fpath.name}")
                failed += 1
        except Exception as exc:
            print(f"  ✘ {fpath.name}: {exc}")
            failed += 1

    print(f"  Procesados: {ok}/{len(files)}")
    if failed:
        print(f"  Fallidos: {failed}")
    return 0
