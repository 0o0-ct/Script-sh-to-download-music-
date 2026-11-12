#!/usr/bin/env python3
"""Lyrics engine — concurrent multi-provider search, evaluation, and embedding.

This is the core lyrics module invoked by the Bash wrapper via:
    py_core lyrics <target> <fmt> [options...]
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Optional

try:
    import mutagen
    from mutagen.mp3 import MP3
    from mutagen.flac import FLAC
    from mutagen.mp4 import MP4, MP4Cover
    from mutagen.id3 import ID3, USLT, SYLT, APIC
    from mutagen.id3 import ID3NoHeaderError
    HAS_MUTAGEN = True
except ImportError:
    HAS_MUTAGEN = False

from otterrip_core.models import LyricsResult, SyncLevel
from otterrip_core.adapters.base import BaseProvider, SongQuery
from otterrip_core.adapters.lrclib import LrclibProvider
from otterrip_core.adapters.multiweb import MultiwebProvider


# ─── Audio helpers ───────────────────────────────────────────────

AUDIO_EXTS = {".mp3", ".flac", ".m4a"}


def _get_audio_file(filepath: str):
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


def _get_tags(audio) -> dict:
    tags: dict = {}
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
            tags["isrc"] = str(audio.tags.get("TSRC", ""))
        elif isinstance(audio, FLAC) and audio.tags:
            tags["title"] = audio.get("title", [""])[0]
            tags["artist"] = audio.get("artist", [""])[0]
            tags["album"] = audio.get("album", [""])[0]
            tags["track"] = audio.get("tracknumber", [""])[0]
            tags["date"] = audio.get("date", [""])[0]
            tags["genre"] = audio.get("genre", [""])[0]
            tags["isrc"] = audio.get("ISRC", [""])[0]
        elif isinstance(audio, MP4) and audio.tags:
            tags["title"] = audio.get("\xa9nam", [""])[0]
            tags["artist"] = audio.get("\xa9ART", [""])[0]
            tags["album"] = audio.get("\xa9alb", [""])[0]
            tags["date"] = audio.get("\xa9day", [""])[0]
            tags["genre"] = audio.get("\xa9gen", [""])[0]
    except Exception:
        pass
    return tags


def _get_duration(audio) -> float | None:
    if audio is None:
        return None
    try:
        if hasattr(audio, "info") and audio.info:
            return audio.info.length
    except Exception:
        pass
    return None


def _has_lyrics(audio) -> bool:
    if audio is None:
        return False
    try:
        if isinstance(audio, MP3) and audio.tags:
            return bool(audio.tags.get("USLT:"))
        elif isinstance(audio, FLAC) and audio.tags:
            return bool(audio.get("LYRICS"))
        elif isinstance(audio, MP4) and audio.tags:
            return bool(audio.tags.get("\xa9lyr"))
    except Exception:
        pass
    return False


# ─── LRC formatting ──────────────────────────────────────────────

def _format_lrc(entries: list[tuple[float, str]]) -> str:
    lines = []
    for ts, text in entries:
        mins = int(ts // 60)
        secs = int(ts % 60)
        ms = int((ts % 1) * 1000)
        lines.append(f"[{mins:02d}:{secs:02d}.{ms:03d}] {text}")
    return "\n".join(lines)


# ─── Embedding ────────────────────────────────────────────────────

def _embed_lyrics(audio, result: LyricsResult, mode: str = "embed") -> bool:
    """Embed lyrics into an audio file. Returns True on success."""
    if audio is None:
        return False

    try:
        if isinstance(audio, MP3):
            if audio.tags is None:
                audio.add_tags()
            audio.tags.delall("USLT:")
            audio.tags.delall("SYLT:")

            if result.has_sync and result.timed_entries:
                # Embed as SYLT (synchronized)
                lrc_text = _format_lrc(result.timed_entries)
                for i, line in enumerate(lrc_text.split("\n")):
                    if line.strip():
                        audio.tags.add(
                            SYLT(
                                encoding=3,
                                lang="eng",
                                text=line,
                                type=1,
                                started=0,
                                syncedType=2,
                                timestamp=[i * 1000],  # approximate
                            )
                        )
            # Always embed USLT as well (plain text fallback)
            plain = result.plain_text or "\n".join(t for _, t in result.timed_entries)
            if plain:
                audio.tags.add(USLT(encoding=3, lang="eng", text=plain.split("\n")))

        elif isinstance(audio, FLAC):
            plain = result.plain_text or "\n".join(t for _, t in result.timed_entries)
            if plain:
                audio["LYRICS"] = plain.split("\n")

        elif isinstance(audio, MP4):
            plain = result.plain_text or "\n".join(t for _, t in result.timed_entries)
            if plain:
                audio.tags["\xa9lyr"] = [plain]

        audio.save()
        return True
    except Exception:
        return False


def _write_lrc_file(audio_path: Path, result: LyricsResult) -> Path | None:
    """Write a .lrc or .txt file alongside the audio."""
    try:
        if result.has_sync and result.timed_entries:
            lrc_path = audio_path.with_suffix(".lrc")
            lrc_text = _format_lrc(result.timed_entries)
            lrc_path.write_text(lrc_text, encoding="utf-8")
            return lrc_path
        else:
            txt_path = audio_path.with_suffix(".txt")
            txt_path.write_text(result.plain_text, encoding="utf-8")
            return txt_path
    except Exception:
        return None


# ─── Provider registry ───────────────────────────────────────────

def _build_providers(
    provider_names: str,
    host: str = "https://lrclib.net",
) -> list[BaseProvider]:
    """Build provider instances from comma-separated names."""
    names = [n.strip().lower() for n in provider_names.split(",") if n.strip()]
    providers: list[BaseProvider] = []
    for name in names:
        if name == "lrclib":
            providers.append(LrclibProvider(host=host))
        elif name == "multiweb":
            p = MultiwebProvider()
            if p.is_available():
                providers.append(p)
    return providers


# ─── Concurrent search ───────────────────────────────────────────

async def _search_one(
    provider: BaseProvider,
    query: SongQuery,
    timeout: float = 10.0,
) -> list[LyricsResult]:
    """Search a single provider with timeout."""
    try:
        return await asyncio.wait_for(provider.search(query), timeout=timeout)
    except asyncio.TimeoutError:
        return []
    except Exception:
        return []


async def _search_all(
    providers: list[BaseProvider],
    query: SongQuery,
    timeout: float = 10.0,
) -> list[LyricsResult]:
    """Search all providers concurrently and collect results."""
    tasks = [
        asyncio.create_task(_search_one(p, query, timeout))
        for p in providers
    ]
    all_results: list[LyricsResult] = []
    for task in tasks:
        try:
            results = await task
            all_results.extend(results)
        except Exception:
            pass
    return all_results


# ─── Result selection ────────────────────────────────────────────

def select_best(
    results: list[LyricsResult],
    query: SongQuery,
    prefer_synced: bool = True,
    plain_ok: bool = False,
) -> Optional[LyricsResult]:
    """Select the best lyrics result from all providers."""
    if not results:
        return None

    # Filter: reject results with very low confidence
    MIN_CONFIDENCE = 0.3
    candidates = [r for r in results if r.confidence >= MIN_CONFIDENCE]
    if not candidates:
        candidates = results  # fallback to all

    # Sort by: sync level (if preferred), then confidence, then entry count
    def sort_key(r: LyricsResult) -> tuple:
        sync_score = r.sync_level if prefer_synced else 0
        return (
            sync_score,
            r.confidence,
            len(r.timed_entries) if r.has_sync else 0,
            len(r.plain_text),
        )

    candidates.sort(key=sort_key, reverse=True)

    best = candidates[0]

    # If we prefer synced but only have plain and plain_ok is False
    if prefer_synced and best.is_plain and not plain_ok:
        synced = [r for r in candidates if r.has_sync]
        if not synced:
            return None  # no synced lyrics available

    return best


# ─── File collection ─────────────────────────────────────────────

def _read_session(session_path: str) -> list[str]:
    """Read verified session file (JSON lines with file paths)."""
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


def _collect_files(target: str, fmt: str, session: str) -> list[Path]:
    """Collect audio files to process."""
    files: list[Path] = []
    ext = f".{fmt}"

    if session:
        for fpath in _read_session(session):
            p = Path(fpath)
            if p.is_file():
                files.append(p)
        return files

    target_path = Path(target)
    if target_path.is_file():
        files.append(target_path)
    elif target_path.is_dir():
        for child in target_path.rglob(f"*{ext}"):
            files.append(child)
        # Also check other common extensions
        for ext2 in AUDIO_EXTS:
            if ext2 != ext:
                for child in target_path.rglob(f"*{ext2}"):
                    if child not in files:
                        files.append(child)

    return sorted(set(files))


# ─── Main entry point ────────────────────────────────────────────

def run_lyrics(args) -> int:
    """Main lyrics command handler."""
    target = args.target
    fmt = args.fmt
    host = args.host
    mode = args.mode
    providers_str = args.providers
    lang = args.lang
    prefer_synced = not args.plain_ok
    upgrade_plain = args.upgrade_plain
    session = args.session
    summary_file = args.summary
    overwrite = args.overwrite

    # Build providers
    providers = _build_providers(providers_str, host=host)
    if not providers:
        print("  No providers available.")
        if summary_file:
            _write_summary(summary_file, 0, 0, 0)
        return 0

    # Collect files
    files = _collect_files(target, fmt, session)
    if not files:
        print("  No files found.")
        if summary_file:
            _write_summary(summary_file, 0, 0, 0)
        return 0

    # Process each file
    found = 0
    notfound = 0
    already = 0
    stats: dict[str, dict] = {}  # per-provider stats

    for provider in providers:
        stats[provider.name] = {"queries": 0, "found": 0, "errors": 0}

    for fpath in files:
        result = _process_file(fpath, providers, lang, prefer_synced, upgrade_plain, overwrite, mode, stats)
        if result == "found":
            found += 1
        elif result == "already":
            already += 1
        else:
            notfound += 1

    # Print summary
    print()
    print(f"  ── Letras procesadas ──")
    print(f"  Encontradas:     {found}")
    print(f"  No encontradas:  {notfound}")
    print(f"  Ya existentes:   {already}")

    # Per-provider stats
    if stats:
        print()
        print("  Fuentes consultadas:")
        for pname, s in stats.items():
            print(f"    {pname}: {s['queries']} consultas, {s['found']} encontradas")

    print()

    if summary_file:
        _write_summary(summary_file, found, notfound, already)

    return 0


def _process_file(
    fpath: Path,
    providers: list[BaseProvider],
    lang: str,
    prefer_synced: bool,
    upgrade_plain: bool,
    overwrite: bool,
    mode: str,
    stats: dict[str, dict],
) -> str:
    """Process a single audio file. Returns 'found', 'already', or 'notfound'."""
    audio = _get_audio_file(str(fpath))
    if audio is None:
        return "notfound"

    tags = _get_tags(audio)
    duration = _get_duration(audio)

    # Check if already has lyrics
    has_lyr = _has_lyrics(audio)
    if has_lyr and not overwrite and not upgrade_plain:
        print(f"  ✔ {fpath.name} — ya tiene letras")
        return "already"

    title = tags.get("title", fpath.stem)
    artist = tags.get("artist", "")
    album = tags.get("album", "")
    isrc = tags.get("isrc", "")

    if not title or not artist:
        print(f"  ⚠ {fpath.name} — metadatos incompletos, saltando")
        return "notfound"

    # Build query
    query = SongQuery(
        title=title,
        artist=artist,
        album=album,
        duration=duration,
        isrc=isrc,
        language=lang,
    )

    # Concurrent search
    loop = asyncio.new_event_loop()
    try:
        all_results = loop.run_until_complete(_search_all(providers, query, timeout=10.0))
    finally:
        loop.close()

    # Update stats
    for r in all_results:
        if r.provider in stats:
            stats[r.provider]["found"] += 1
    for p in providers:
        stats[p.name]["queries"] += 1

    if not all_results:
        print(f"  ✘ {fpath.name} — no se encontraron letras")
        return "notfound"

    # Select best
    best = select_best(all_results, query, prefer_synced=prefer_synced, plain_ok=not prefer_synced)
    if best is None:
        print(f"  ✘ {fpath.name} — sin letras sincronizadas (usa --plain-ok para aceptar planas)")
        return "notfound"

    # Embed or write file
    sync_label = {
        SyncLevel.NONE: "plana",
        SyncLevel.LINES: "sinc. líneas",
        SyncLevel.WORDS: "sinc. palabras",
        SyncLevel.SYLLABLES: "sinc. sílabas",
    }.get(best.sync_level, "desconocida")

    if mode == "embed":
        if _embed_lyrics(audio, best, mode):
            print(f"  ✔ {fpath.name} — letra {sync_label} ({best.provider})")
        else:
            print(f"  ⚠ {fpath.name} — error al incrustar")
            return "notfound"
    else:
        # File mode
        out = _write_lrc_file(fpath, best)
        if out:
            print(f"  ✔ {fpath.name} — {out.name} ({best.provider})")
        else:
            print(f"  ⚠ {fpath.name} — error al escribir archivo")
            return "notfound"

    return "found"


def _write_summary(path: str, found: int, notfound: int, already: int) -> None:
    """Write summary file for Bash wrapper."""
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            f"found={found}\nnotfound={notfound}\nalready={already}\n",
            encoding="utf-8",
        )
    except Exception:
        pass
