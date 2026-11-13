#!/usr/bin/env python3
"""Lyrics engine — per-file concurrent fallback search and embedding.

This is the core lyrics module invoked by the Bash wrapper via:
    py_core lyrics <target> <fmt> [options...]
"""
from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from pathlib import Path
from typing import Awaitable, Callable, Optional

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
            return bool(audio.tags.get("USLT:") or audio.tags.get("SYLT:"))
        elif isinstance(audio, FLAC) and audio.tags:
            return bool(audio.get("LYRICS") or audio.get("SYNCEDLYRICS"))
        elif isinstance(audio, MP4) and audio.tags:
            return bool(
                audio.tags.get("\xa9lyr")
                or audio.tags.get(ITUNES_SYNCED_ATOM)
            )
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

# Átomo libre de iTunes donde se guarda la letra sincronizada en MP4/M4A.
# Es la convención que usan Apple Music, iTunes y Picard.
ITUNES_SYNCED_ATOM = "----:com.apple.iTunes:LYRICS"


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
                synced_text = [
                    (text, round(timestamp * 1000))
                    for timestamp, text in result.timed_entries
                    if text.strip()
                ]
                if synced_text:
                    audio.tags.add(
                        SYLT(
                            encoding=3,
                            lang="eng",
                            format=2,
                            type=1,
                            text=synced_text,
                        )
                    )

            plain = result.plain_text or "\n".join(t for _, t in result.timed_entries)
            if plain:
                audio.tags.add(USLT(encoding=3, lang="eng", text=plain))

        elif isinstance(audio, FLAC):
            if result.has_sync and result.timed_entries:
                # Cuando existe letra sincronizada, escribimos el contenido
                # con timestamps LRC directamente en la etiqueta estándar
                # LYRICS — así los reproductores que solo leen LYRICS pueden
                # mostrar la letra sincronizada. Se mantiene SYNCEDLYRICS
                # por compatibilidad con reproductores que lo soportan.
                lrc_text = _format_lrc(result.timed_entries)
                audio["LYRICS"] = lrc_text.split("\n")
                audio["SYNCEDLYRICS"] = [lrc_text]
            else:
                plain = result.plain_text or "\n".join(t for _, t in result.timed_entries)
                if plain:
                    audio["LYRICS"] = plain.split("\n")

        elif isinstance(audio, MP4):
            if result.has_sync and result.timed_entries:
                # Cuando existe letra sincronizada, escribimos el contenido
                # completo con timestamps LRC directamente en el átomo
                # estándar ©lyr — así los reproductores que solo leen ©lyr
                # pueden mostrar la letra sincronizada. Se mantiene el átomo
                # libre de iTunes por compatibilidad con Apple Music/iTunes.
                lrc_text = _format_lrc(result.timed_entries)
                audio.tags["\xa9lyr"] = [lrc_text]
                try:
                    audio.tags.pop(ITUNES_SYNCED_ATOM, None)
                except Exception:
                    pass
                audio.tags[ITUNES_SYNCED_ATOM] = [lrc_text.encode("utf-8")]
            else:
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
    """Build providers in the requested fallback order."""
    names = [n.strip().lower() for n in provider_names.split(",") if n.strip()]
    providers: list[BaseProvider] = []
    multiweb_sources = tuple(
        source for source in MultiwebProvider.DEFAULT_SOURCES
        if source != "lrclib" or "lrclib" not in names
    )
    for name in names:
        if name == "lrclib":
            providers.append(LrclibProvider(host=host))
        elif name == "multiweb":
            p = MultiwebProvider(sources=multiweb_sources)
            if p.is_available():
                providers.append(p)
    return providers


# ─── Provider fallback ───────────────────────────────────────────

async def _search_one(
    provider: BaseProvider,
    query: SongQuery,
    timeout: float = 10.0,
) -> list[LyricsResult]:
    """Search a single provider with timeout."""
    try:
        provider_timeout = timeout
        if isinstance(provider, MultiwebProvider):
            provider_timeout *= len(provider.sources)
        return await asyncio.wait_for(provider.search(query), timeout=provider_timeout)
    except asyncio.TimeoutError:
        return []
    except Exception:
        return []


async def _search_cascade(
    providers: list[BaseProvider],
    query: SongQuery,
    prefer_synced: bool,
    plain_ok: bool,
    stats: dict[str, dict] | None = None,
    timeout: float = 10.0,
) -> Optional[LyricsResult]:
    """Try providers in configured order until one returns valid lyrics."""
    for provider in providers:
        if stats is not None:
            stats[provider.name]["queries"] += 1
        results = await _search_one(provider, query, timeout)
        best = select_best(
            results,
            query,
            prefer_synced=prefer_synced,
            plain_ok=plain_ok,
        )
        if best is not None:
            if stats is not None:
                stats[provider.name]["found"] += 1
            return best
    return None


async def _process_files_concurrently(
    files: list[Path],
    processor: Callable[[Path], Awaitable[str]],
    jobs: int,
) -> list[str]:
    """Run per-file processors concurrently without exceeding the worker limit."""
    semaphore = asyncio.Semaphore(max(1, jobs))

    async def process_one(path: Path) -> str:
        async with semaphore:
            return await processor(path)

    return await asyncio.gather(*(process_one(path) for path in files))


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

    candidates = [
        result
        for result in results
        if result.confidence >= 0.3
        and _has_useful_lyrics(result)
        and _matches_query(result, query)
        and (not prefer_synced or plain_ok or result.has_sync)
    ]
    if not candidates:
        return None

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
    return candidates[0]


def _has_useful_lyrics(result: LyricsResult) -> bool:
    if result.has_sync:
        return any(text.strip() for _, text in result.timed_entries)
    return bool(result.plain_text.strip())


def _normalize_match_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value).casefold()
    return "".join(
        character
        for character in decomposed
        if not unicodedata.combining(character) and character.isalnum()
    )


def _matches_query(result: LyricsResult, query: SongQuery) -> bool:
    if query.isrc and result.isrc:
        return result.isrc.casefold() == query.isrc.casefold()

    for actual, expected in ((result.title, query.title), (result.artist, query.artist)):
        if actual and expected and _normalize_match_text(actual) != _normalize_match_text(expected):
            return False
    return True


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
        return sorted(set(files))

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

    stats: dict[str, dict] = {}  # per-provider stats

    for provider in providers:
        stats[provider.name] = {"queries": 0, "found": 0, "errors": 0}

    async def process_file(fpath: Path) -> str:
        return await _process_file(
            fpath,
            providers,
            lang,
            prefer_synced,
            upgrade_plain,
            overwrite,
            mode,
            stats,
        )

    results = asyncio.run(
        _process_files_concurrently(
            files,
            process_file,
            jobs=getattr(args, "jobs", 3),
        )
    )
    found = results.count("found")
    already = results.count("already")
    notfound = len(results) - found - already

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


async def _process_file(
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
        prefer_synced=prefer_synced,
    )

    best = await _search_cascade(
        providers,
        query,
        prefer_synced=prefer_synced,
        plain_ok=not prefer_synced,
        stats=stats,
        timeout=10.0,
    )
    if best is None:
        print(f"  ✘ {fpath.name} — no se encontraron letras")
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
            # Generar archivo .lrc externo como respaldo universal para
            # reproductores estrictos que no leen metadatos sincronizados.
            if best.has_sync and best.timed_entries:
                lrc_path = fpath.with_suffix(".lrc")
                try:
                    lrc_path.write_text(
                        _format_lrc(best.timed_entries), encoding="utf-8"
                    )
                    print(f"  ✔ {fpath.name} — letra {sync_label} + {lrc_path.name} ({best.provider})")
                except Exception:
                    print(f"  ✔ {fpath.name} — letra {sync_label} ({best.provider})")
            else:
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
