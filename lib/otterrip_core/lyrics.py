#!/usr/bin/env python3
"""Lyrics engine — per-file concurrent fallback search and embedding.

This is the core lyrics module invoked by the Bash wrapper via:
    py_core lyrics <target> <fmt> [options...]

Part 1 (this file): obtención, selección, validación y normalización
de letras. Produce un :class:`LyricsBundle` por pista.  La escritura de
tags/archivos (Parte 2) se mantiene sin cambios mediante compatibilidad
con el formato legacy ``LyricsResult``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import unicodedata
from pathlib import Path
from typing import Awaitable, Callable, Optional

try:
    import mutagen
    from mutagen.mp3 import MP3
    from mutagen.flac import FLAC
    from mutagen.mp4 import MP4, MP4Cover
    from mutagen.id3 import ID3, USLT, SYLT, APIC, TXXX
    from mutagen.id3 import ID3NoHeaderError
    HAS_MUTAGEN = True
except ImportError:
    HAS_MUTAGEN = False

from otterrip_core.models import LyricsResult, SyncLevel
from otterrip_core.adapters.base import SongQuery
from otterrip_core.lyrics_bundle import (
    LyricsBundle,
    bundle_from_legacy,
)
from otterrip_core.lrc_validator import validate_and_normalise_lrc
from otterrip_core.query_clean import build_query_terms, clean_title, clean_artist
from otterrip_core.lrc_cache import LyricsCache
from otterrip_core.offset import apply_offset_to_bundle
from otterrip_core.adapters.base import BaseProvider
from otterrip_core.adapters.lrclib import LrclibProvider
from otterrip_core.adapters.multiweb import MultiwebProvider


logger = logging.getLogger("otterrip_core.lyrics")

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


# ─── LRC formatting (legacy — Part 2 keeps this) ─────────────────

def _format_lrc(entries: list[tuple[float, str]]) -> str:
    lines = []
    for ts, text in entries:
        mins = int(ts // 60)
        secs = int(ts % 60)
        ms = int((ts % 1) * 1000)
        # 2-digit centiseconds for BlackPlayer/PowerAmp compatibility
        lines.append(f"[{mins:02d}:{secs:02d}.{ms // 10:02d}] {text}")
    return "\n".join(lines)


# ─── Embedding (legacy — Part 2, untouched) ──────────────────────

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
            # Remove old TXXX:LYRICS if present
            audio.tags.delall("TXXX:LYRICS")

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
                # TXXX:LYRICS with LRC format — PowerAmp reads synced lyrics from here
                lrc_text = _format_lrc(result.timed_entries)
                if lrc_text:
                    audio.tags.add(
                        TXXX(encoding=3, desc="LYRICS", text=lrc_text)
                    )

            plain = result.plain_text or "\n".join(t for _, t in result.timed_entries)
            if plain:
                audio.tags.add(USLT(encoding=3, lang="eng", text=plain))

        elif isinstance(audio, FLAC):
            if result.has_sync and result.timed_entries:
                lrc_text = _format_lrc(result.timed_entries)
                audio["LYRICS"] = lrc_text.split("\n")
                audio["SYNCEDLYRICS"] = [lrc_text]
            else:
                plain = result.plain_text or "\n".join(t for _, t in result.timed_entries)
                if plain:
                    audio["LYRICS"] = plain.split("\n")

        elif isinstance(audio, MP4):
            if result.has_sync and result.timed_entries:
                lrc_text = _format_lrc(result.timed_entries)
                audio["\xa9lyr"] = [lrc_text]
                audio[ITUNES_SYNCED_ATOM] = [lrc_text.encode("utf-8")]
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
) -> list[LyricsBundle]:
    """Search a single provider with timeout.  Returns LyricsBundle list."""
    try:
        provider_timeout = timeout
        # Multiweb tries several sources sequentially; scale timeout.
        if isinstance(provider, MultiwebProvider):
            provider_timeout *= len(provider.sources)
        return await asyncio.wait_for(
            provider.search(query), timeout=provider_timeout
        )
    except asyncio.TimeoutError:
        logger.warning("timeout en proveedor %s", provider.name)
        return []
    except Exception as exc:
        logger.warning("error en proveedor %s: %s", provider.name, exc)
        return []


async def _search_cascade(
    providers: list[BaseProvider],
    query: SongQuery,
    prefer_synced: bool,
    plain_ok: bool,
    stats: dict[str, dict] | None = None,
    timeout: float = 10.0,
    cache: Optional[LyricsCache] = None,
) -> Optional[LyricsBundle]:
    """Try providers in order until one returns a usable LyricsBundle.

    Uses the local cache first; on miss, queries providers and caches the
    best bundle.
    """
    # --- Cache lookup --------------------------------------------------
    if cache is not None:
        cached = cache.get(query.artist, query.title, None)
        if cached is not None:
            # Re-hydrate bundle from cached dict.
            bundle = LyricsBundle(
                kind=cached.get("kind", "none"),
                lrc=cached.get("lrc"),
                plain=cached.get("plain"),
                lines=[(t, x) for t, x in cached.get("lines", [])],
                source=cached.get("source", ""),
                audio_duration_ms=cached.get("audio_duration_ms"),
                ref_duration_ms=cached.get("ref_duration_ms"),
                offset_ms=cached.get("offset_ms", 0),
                confidence=cached.get("confidence", 0.0),
                warnings=cached.get("warnings", []),
            )
            if stats is not None:
                stats[providers[0].name if providers else "cache"]["hits"] = (
                    stats.get(providers[0].name if providers else "cache", {}).get("hits", 0) + 1
                )
            logger.info("caché HIT: %s - %s", query.artist, query.title)
            return bundle

    for provider in providers:
        if stats is not None:
            stats.setdefault(provider.name, {"queries": 0, "found": 0, "errors": 0})
            stats[provider.name]["queries"] += 1
        try:
            results = await _search_one(provider, query, timeout)
        except Exception as exc:
            logger.error("proveedor %s falló: %s", provider.name, exc)
            if stats is not None:
                stats[provider.name]["errors"] += 1
            continue

        # Detect input type: LyricsResult (legacy/test) or LyricsBundle (new).
        from otterrip_core.models import LyricsResult as _LR
        from otterrip_core.lyrics_bundle import LyricsBundle as _LB, bundle_from_legacy

        are_results = all(isinstance(r, _LR) for r in results)
        are_bundles = all(isinstance(r, _LB) for r in results)

        if are_results:
            result_bundles = [
                bundle_from_legacy(
                    provider=r.provider,
                    title=r.title,
                    artist=r.artist,
                    album=r.album,
                    duration_s=r.duration,
                    sync_level=int(r.sync_level),
                    plain_text=r.plain_text,
                    timed_entries=r.timed_entries,
                    confidence=r.confidence,
                    source=r.provider,
                    ref_duration_ms=(
                        int(round(r.duration * 1000)) if r.duration else None
                    ),
                )
                for r in results
            ]
            best_bundle = _select_best_bundle(
                result_bundles,
                prefer_synced=prefer_synced,
                plain_ok=plain_ok,
                query=query,
                query_duration_s=query.duration,
            )
            # Return the original LyricsResult for backward compatibility.
            best = None
            if best_bundle is not None:
                for r in results:
                    if (
                        r.provider == best_bundle.provider
                        and r.title == best_bundle.title
                        and r.artist == best_bundle.artist
                    ):
                        best = r
                        break
        elif are_bundles:
            best = _select_best_bundle(
                results,
                prefer_synced=prefer_synced,
                plain_ok=plain_ok,
                query=query,
                query_duration_s=query.duration,
            )
        else:
            best = None

        if best is not None:
            if stats is not None:
                stats[provider.name]["found"] += 1
            # --- Cache store -------------------------------------------
            if cache is not None:
                # Extract bundle fields regardless of type.
                if isinstance(best, LyricsBundle):
                    cache_payload = {
                        "kind": best.kind,
                        "lrc": best.lrc,
                        "plain": best.plain,
                        "lines": best.lines,
                        "source": best.source,
                        "audio_duration_ms": best.audio_duration_ms,
                        "ref_duration_ms": best.ref_duration_ms,
                        "offset_ms": best.offset_ms,
                        "confidence": best.confidence,
                        "warnings": best.warnings,
                    }
                    kind_for_log = best.kind
                    conf_for_log = best.confidence
                else:
                    # Legacy LyricsResult — synthesize bundle fields.
                    from otterrip_core.lyrics_bundle import bundle_from_legacy

                    tmp = bundle_from_legacy(
                        provider=best.provider,
                        title=best.title,
                        artist=best.artist,
                        album=best.album,
                        duration_s=best.duration,
                        sync_level=int(best.sync_level),
                        plain_text=best.plain_text,
                        timed_entries=best.timed_entries,
                        confidence=best.confidence,
                        source=best.provider,
                    )
                    cache_payload = {
                        "kind": tmp.kind,
                        "lrc": tmp.lrc,
                        "plain": tmp.plain,
                        "lines": tmp.lines,
                        "source": tmp.source,
                        "audio_duration_ms": tmp.audio_duration_ms,
                        "ref_duration_ms": tmp.ref_duration_ms,
                        "offset_ms": tmp.offset_ms,
                        "confidence": tmp.confidence,
                        "warnings": tmp.warnings,
                    }
                    kind_for_log = tmp.kind
                    conf_for_log = tmp.confidence
                cache.set(
                    query.artist,
                    query.title,
                    cache_payload,
                    duration_ms=cache_payload["audio_duration_ms"],
                )
            logger.info(
                "letra encontrada: %s - %s | proveedor=%s | kind=%s | conf=%.2f",
                query.artist, query.title, provider.name,
                getattr(best, "kind", "unknown"), getattr(best, "confidence", 0.0),
            )
            return best

    logger.warning("no se encontraron letras: %s - %s", query.artist, query.title)
    return None


def _normalize_match_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value).casefold()
    return "".join(
        ch
        for ch in decomposed
        if not unicodedata.combining(ch) and ch.isalnum()
    )


def _bundle_matches_query(bundle: LyricsBundle, query: SongQuery) -> bool:
    """Check title/artist compatibility between bundle and query."""
    if query.isrc and bundle.isrc:
        return bundle.isrc.casefold() == query.isrc.casefold()
    for actual, expected in (
        (bundle.title, query.title),
        (bundle.artist, query.artist),
    ):
        if actual and expected:
            if _normalize_match_text(actual) != _normalize_match_text(expected):
                return False
    return True


def _select_best_bundle(
    bundles: list[LyricsBundle],
    prefer_synced: bool = True,
    plain_ok: bool = False,
    query: Optional[SongQuery] = None,
    query_duration_s: Optional[float] = None,
) -> Optional[LyricsBundle]:
    """Select the best LyricsBundle from a provider's results.

    Rule: any synced (line/word) beats any plain.  Never degrade.
    """
    if not bundles:
        return None

    candidates = [
        b for b in bundles
        if b.confidence >= 0.3
        and _bundle_has_lyrics(b)
        and (not prefer_synced or plain_ok or b.has_sync)
        and (query is None or _bundle_matches_query(b, query))
    ]
    if not candidates:
        return None

    def sort_key(b: LyricsBundle) -> tuple:
        sync_score = 1 if (b.has_sync and prefer_synced) else 0
        dur_delta = b.duration_delta_ms() or 10_000_000
        return (
            sync_score,
            -dur_delta,
            b.confidence,
            len(b.lines) if b.has_sync else len(b.plain or ""),
        )

    candidates.sort(key=sort_key, reverse=True)
    return candidates[0]


def select_best(
    results: list[LyricsResult],
    query: SongQuery,
    prefer_synced: bool = True,
    plain_ok: bool = False,
) -> Optional[LyricsResult]:
    """Backward-compatible wrapper: selects best legacy LyricsResult.

    Converts LyricsResult objects to LyricsBundle, selects the best,
    and returns the original LyricsResult wrapper for the caller.
    """
    from otterrip_core.lyrics_bundle import bundle_from_legacy

    if not results:
        return None

    bundles = [
        bundle_from_legacy(
            provider=r.provider,
            title=r.title,
            artist=r.artist,
            album=r.album,
            duration_s=r.duration,
            sync_level=int(r.sync_level),
            plain_text=r.plain_text,
            timed_entries=r.timed_entries,
            confidence=r.confidence,
            source=r.provider,
            ref_duration_ms=(
                int(round(r.duration * 1000)) if r.duration else None
            ),
        )
        for r in results
    ]
    best = _select_best_bundle(
        bundles,
        prefer_synced=prefer_synced,
        plain_ok=plain_ok,
        query=query,
        query_duration_s=query.duration,
    )
    if best is None:
        return None
    # Return the matching original LyricsResult (identity preserved).
    best_provider = best.provider if best.source else ""
    best_title = best.title
    best_artist = best.artist
    for r in results:
        if (
            r.provider == best_provider
            and r.title == best_title
            and r.artist == best_artist
        ):
            return r
    return results[0]


def _bundle_has_lyrics(bundle: LyricsBundle) -> bool:
    if bundle.has_sync:
        return any(text.strip() for _, text in bundle.lines)
    return bool(bundle.plain and bundle.plain.strip())


# ─── File collection ─────────────────────────────────────────────

def _read_session(session_path: str) -> list[str]:
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
        for ext2 in AUDIO_EXTS:
            if ext2 != ext:
                for child in target_path.rglob(f"*{ext2}"):
                    if child not in files:
                        files.append(child)

    return sorted(set(files))


# ─── Per-file processing ─────────────────────────────────────────

async def _process_file(
    fpath: Path,
    providers: list[BaseProvider],
    lang: str,
    prefer_synced: bool,
    upgrade_plain: bool,
    overwrite: bool,
    mode: str,
    stats: dict[str, dict],
    cache: Optional[LyricsCache] = None,
    sync_level: str = "line",
    sidecar: bool = False,
    embed_style: str = "lrc",
    min_confidence: float = 0.0,
    offset_ms: int = 0,
) -> str:
    """Process a single audio file. Returns 'found', 'already', or 'notfound'."""
    audio = _get_audio_file(str(fpath))
    if audio is None:
        return "notfound"

    tags = _get_tags(audio)
    duration = _get_duration(audio)

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

    # --- Clean query terms (Phase 1: limpieza de consulta) ------------
    terms = build_query_terms(title, artist, album)
    clean_t = terms["title"]
    clean_a = terms["artist"]
    clean_alb = terms["album"]

    query = SongQuery(
        title=clean_t,
        artist=clean_a,
        album=clean_alb,
        duration=duration,
        isrc=isrc,
        language=lang,
        prefer_synced=prefer_synced,
    )

    best = await _search_cascade(
        providers,
        query,
        prefer_synced=prefer_synced,
        plain_ok=(not prefer_synced) or upgrade_plain,
        stats=stats,
        timeout=10.0,
        cache=cache,
    )
    if best is None:
        print(f"  ✘ {fpath.name} — no se encontraron letras")
        return "notfound"

    # Confidence gate: skip if below threshold
    if min_confidence > 0 and best.confidence < min_confidence:
        print(
            f"  ⚠ {fpath.name} — confianza {best.confidence:.2f} < {min_confidence:.2f}, omitida"
        )
        best.warnings.append(f"confianza {best.confidence:.2f} < mínimo {min_confidence:.2f}")
        return "notfound"

    # Apply offset if requested
    if offset_ms != 0 and best.lines:
        from otterrip_core.offset import apply_offset_to_bundle
        best.lines = [(max(0, ms + offset_ms), txt) for ms, txt in best.lines]
        best.lines.sort(key=lambda x: x[0])
        best.offset_ms += offset_ms
        if best.lrc:
            from otterrip_core.lrc_validator import shift_lrc
            best.lrc = shift_lrc(best.lrc, offset_ms)

    # Log decision.
    print(
        f"  ✔ {fpath.name} — letra {best.kind} "
        f"(conf={best.confidence:.2f}, origen={best.source})"
    )
    if best.warnings:
        for w in best.warnings:
            print(f"    ⚠ {w}")

    # Convert to legacy LyricsResult for Part 2 embedding/writing.
    legacy = best.to_legacy_result(provider=best.source.split(" | ")[0] if best.source else "unknown")
    # Override fields from bundle for accuracy.
    legacy["title"] = clean_t
    legacy["artist"] = clean_a
    legacy["album"] = clean_alb

    # Build a lightweight LyricsResult-like object for legacy functions.
    class _LegacyResult:
        def __init__(self, d):
            self.__dict__.update(d)

    result = _LegacyResult(legacy)

    if mode == "embed":
        if _embed_lyrics(audio, result, mode):
            # Sidecar .lrc: always generate when sidecar=True or when synced
            should_write_sidecar = sidecar or (best.has_sync and best.lines)
            if should_write_sidecar:
                lrc_path = fpath.with_suffix(".lrc")
                try:
                    if best.has_sync and best.lines:
                        lrc_entries = [(ms / 1000.0, txt) for ms, txt in best.lines]
                        lrc_content = _format_lrc(lrc_entries)
                    else:
                        # Plain lyrics sidecar (no timestamps)
                        lrc_content = best.plain or "\n".join(t for _, t in best.lines) if best.lines else ""
                    if lrc_content:
                        lrc_path.write_text(lrc_content, encoding="utf-8")
                    # Enhanced LRC sidecar for word-level
                    if best.kind == "word" and sidecar:
                        elrc_path = fpath.with_suffix(".elrc")
                        if best.lrc:
                            elrc_path.write_text(best.lrc, encoding="utf-8")
                    print(f"  ✔ {fpath.name} — letra {best.kind} + {lrc_path.name}")
                except Exception:
                    print(f"  ✔ {fpath.name} — letra {best.kind}")
            else:
                print(f"  ✔ {fpath.name} — letra {best.kind}")
        else:
            print(f"  ⚠ {fpath.name} — error al incrustar")
            return "notfound"
    else:
        out = _write_lrc_file(fpath, result)
        if out:
            print(f"  ✔ {fpath.name} — {out.name}")
        else:
            print(f"  ⚠ {fpath.name} — error al escribir archivo")
            return "notfound"

    return "found"


def _write_summary(path: str, found: int, notfound: int, already: int) -> None:
    try:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            f"found={found}\nnotfound={notfound}\nalready={already}\n",
            encoding="utf-8",
        )
    except Exception:
        pass


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
    sync_level = getattr(args, "sync_level", "line")
    sidecar = getattr(args, "sidecar", False)
    embed_style = getattr(args, "embed_style", "lrc")
    min_confidence = getattr(args, "min_confidence", 0.0)
    offset_ms = getattr(args, "offset_ms", 0)
    session = args.session
    summary_file = args.summary
    overwrite = args.overwrite

    # Build providers
    providers = _build_providers(providers_str, host=host)
    if not providers:
        print("  No hay proveedores disponibles.")
        if summary_file:
            _write_summary(summary_file, 0, 0, 0)
        return 0

    # Collect files
    files = _collect_files(target, fmt, session)
    if not files:
        print("  No se encontraron archivos.")
        if summary_file:
            _write_summary(summary_file, 0, 0, 0)
        return 0

    stats: dict[str, dict] = {}
    for provider in providers:
        stats[provider.name] = {"queries": 0, "found": 0, "errors": 0}

    # Local cache (artist + title + duration).
    cache_dir = getattr(args, "cache_dir", "") or str(
        Path.home() / ".cache" / "otterrip" / "lyrics"
    )
    cache = LyricsCache(cache_dir)

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
            cache=cache,
            sync_level=sync_level,
            sidecar=sidecar,
            embed_style=embed_style,
            min_confidence=min_confidence,
            offset_ms=offset_ms,
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

    print()
    print("  ── Letras procesadas ──")
    print(f"  Encontradas:     {found}")
    print(f"  No encontradas:  {notfound}")
    print(f"  Ya existentes:   {already}")

    if stats:
        print()
        print("  Fuentes consultadas:")
        for pname, s in stats.items():
            print(f"    {pname}: {s['queries']} consultas, {s['found']} encontradas")

    print()

    if summary_file:
        _write_summary(summary_file, found, notfound, already)

    return 0


# ─── Concurrent file processing (kept from original) ─────────────

async def _process_files_concurrently(
    files: list[Path],
    processor: Callable[[Path], Awaitable[str]],
    jobs: int,
) -> list[str]:
    semaphore = asyncio.Semaphore(max(1, jobs))

    async def process_one(path: Path) -> str:
        async with semaphore:
            return await processor(path)

    return await asyncio.gather(*(process_one(path) for path in files))
