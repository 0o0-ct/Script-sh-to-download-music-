#!/usr/bin/env python3
"""Lyrics verify / repair / shift — CLI subcommands for library management.

Implements the Parte 2 contract:

    lyrics verify <dir>   — read-only audit; OK / WARN / FAIL per track.
    lyrics repair <dir>   — re-embed from sidecar or regenerate; --dry-run first.
    lyrics shift <file> <+/-ms> — adjust offset on tag AND sidecar.

Reuses: lrc_validator, lyrics_bundle, offset.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from dataclasses import dataclass, field

try:
    import mutagen
    from mutagen.mp3 import MP3
    from mutagen.flac import FLAC
    from mutagen.mp4 import MP4
    from mutagen.id3 import ID3, USLT, SYLT, ID3NoHeaderError
    HAS_MUTAGEN = True
except ImportError:
    HAS_MUTAGEN = False

from otterrip_core.lrc_validator import (
    parse_lrc,
    validate_and_normalise_lrc,
    normalise_lrc,
)
from otterrip_core.offset import apply_offset_to_bundle as _shift_lrc_text


# ── Constants ──────────────────────────────────────────────────────

AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav"}

ITUNES_SYNCED_ATOM = "----:com.apple.iTunes:LYRICS"


# ── Data classes ───────────────────────────────────────────────────

@dataclass
class TrackVerdict:
    """Result of verifying one audio file."""
    file: str
    status: str          # "OK" | "WARN" | "FAIL"
    has_embedded: bool = False
    has_sidecar: bool = False
    has_synced: bool = False
    has_plain: bool = False
    kind: str = "none"   # "synced" | "plain" | "none"
    issues: list[str] = field(default_factory=list)


# ── Audio I/O helpers ─────────────────────────────────────────────

def _open_audio(filepath: str):
    """Open an audio file with mutagen. Returns None on failure."""
    if not HAS_MUTAGEN:
        return None
    try:
        ext = Path(filepath).suffix.lower()
        if ext == ".mp3":
            return MP3(filepath)
        elif ext in (".flac",):
            return FLAC(filepath)
        elif ext == ".m4a":
            return MP4(filepath)
        return mutagen.File(filepath)
    except Exception:
        return None


def _extract_embedded_lyrics(audio) -> tuple[str | None, str | None]:
    """Extract (synced_lrc_text, plain_text) from embedded tags.

    Returns (synced, plain).  Either may be None.
    """
    synced = None
    plain = None
    try:
        if isinstance(audio, MP3) and audio.tags:
            # SYLT (synced)
            for frame in audio.tags.getall("SYLT:"):
                parts = []
                for txt, ms in frame.text:
                    m, s = divmod(ms // 1000, 60)
                    ms_part = ms % 1000
                    parts.append(f"[{m:02d}:{s:02d}.{ms_part // 10:02d}] {txt}")
                if parts:
                    synced = "\n".join(parts)
                    break
            # USLT (plain)
            for frame in audio.tags.getall("USLT:"):
                plain = "\n".join(str(t) for t in frame.text) if frame.text else None
                break

        elif isinstance(audio, FLAC) and audio.tags:
            # SYNCEDLYRICS or LYRICS
            sl = audio.get("SYNCEDLYRICS")
            if sl:
                synced = "\n".join(sl)
            lr = audio.get("LYRICS")
            if lr:
                candidate = "\n".join(lr)
                # Heuristic: if it has timestamps, treat as synced
                if re.search(r"\[\d{2}:\d{2}", candidate):
                    synced = candidate
                else:
                    plain = candidate

        elif isinstance(audio, MP4) and audio.tags:
            lyr = audio.get("\xa9lyr")
            if lyr:
                candidate = str(lyr[0]) if lyr else ""
                if re.search(r"\[\d{2}:\d{2}", candidate):
                    synced = candidate
                else:
                    plain = candidate
            sl = audio.get(ITUNES_SYNCED_ATOM)
            if sl:
                synced = sl[0].decode("utf-8", errors="replace") if sl else None
    except Exception:
        pass
    return synced, plain


def _is_lrc_text(text: str) -> bool:
    """Heuristic: does text look like LRC (has timestamps)?"""
    return bool(re.search(r"\[\d{1,2}:\d{2}(?:\.\d+)?\]", text))


def _get_duration(audio) -> float | None:
    if audio is None:
        return None
    try:
        if hasattr(audio, "info") and audio.info:
            return audio.info.length
    except Exception:
        pass
    return None


# ── VERIFY ─────────────────────────────────────────────────────────

def verify_track(filepath: Path) -> TrackVerdict:
    """Read-only audit of one audio file's lyrics state."""
    v = TrackVerdict(file=str(filepath), status="OK")
    audio = _open_audio(str(filepath))
    sidecar = filepath.with_suffix(".lrc")

    # Check sidecar
    if sidecar.is_file():
        v.has_sidecar = True
        try:
            sidecar_text = sidecar.read_text(encoding="utf-8", errors="replace")
            if _is_lrc_text(sidecar_text):
                v.has_synced = True
                entries = parse_lrc(sidecar_text)
                if not entries:
                    v.issues.append("sidecar .lrc sin timestamps válidos")
                    v.status = "WARN"
                else:
                    v.kind = "synced"
            else:
                v.has_plain = True
                v.kind = "plain"
        except Exception:
            v.issues.append("sidecar .lrc no se pudo leer")
            v.status = "WARN"

    # Check embedded tags
    if audio is not None:
        synced, plain = _extract_embedded_lyrics(audio)
        if synced:
            v.has_embedded = True
            v.has_synced = True
            # Validate synced content
            result = validate_and_normalise_lrc(
                synced, audio_duration_s=_get_duration(audio)
            )
            if not result.valid:
                v.issues.append(f"tag SYLT/LYRICS inválido: {result.reason}")
                if v.status == "OK":
                    v.status = "WARN"
            else:
                v.kind = "synced"
                if result.warnings:
                    v.issues.extend(result.warnings)
                    if v.status == "OK":
                        v.status = "WARN"
        elif plain:
            v.has_embedded = True
            v.has_plain = True
            if v.kind == "none":
                v.kind = "plain"
        else:
            # No embedded lyrics at all
            if not v.has_sidecar:
                v.status = "FAIL"
                v.issues.append("sin letra (ni tag ni sidecar)")
    else:
        # Could not open audio
        if not v.has_sidecar:
            v.status = "FAIL"
            v.issues.append("no se pudo abrir el archivo de audio")
        else:
            v.status = "WARN"
            v.issues.append("no se pudo abrir el audio (sidecar existe)")

    # Orphan sidecar check
    if v.has_sidecar and not v.has_embedded:
        v.issues.append("sidecar .lrc huérfano (sin tag embebido)")
        if v.status == "OK":
            v.status = "WARN"

    return v


def run_lyrics_verify(args) -> int:
    """lyrics verify <dir> — read-only audit."""
    target = Path(args.path)
    if not target.exists():
        print(f"Ruta no encontrada: {args.path}", file=sys.stderr)
        return 1

    files = sorted(
        f for f in target.rglob("*")
        if f.is_file() and f.suffix.lower() in AUDIO_EXTS
    )
    if not files:
        print("No se encontraron archivos de audio.")
        return 0

    verdicts: list[TrackVerdict] = []
    for f in files:
        v = verify_track(f)
        verdicts.append(v)

    # Per-track report
    ok_count = 0
    warn_count = 0
    fail_count = 0
    synced_count = 0
    plain_count = 0
    none_count = 0

    print()
    print(f"  ── Verificación de letras: {target} ──")
    print()

    for v in verdicts:
        icon = {"OK": "✔", "WARN": "⚠", "FAIL": "✘"}.get(v.status, "?")
        color = {"OK": "GREEN", "WARN": "YELLOW", "FAIL": "RED"}.get(v.status, "")
        print(f"  {icon} [{v.status}] {Path(v.file).name}  (kind={v.kind})")
        if v.issues:
            for issue in v.issues:
                print(f"      • {issue}")

        if v.status == "OK":
            ok_count += 1
        elif v.status == "WARN":
            warn_count += 1
        else:
            fail_count += 1

        if v.kind == "synced":
            synced_count += 1
        elif v.kind == "plain":
            plain_count += 1
        else:
            none_count += 1

    total = len(verdicts)
    print()
    print(f"  Total: {total}")
    print(f"  OK:    {ok_count}")
    print(f"  WARN:  {warn_count}")
    print(f"  FAIL:  {fail_count}")
    print()
    print(f"  ── Contadores ──")
    print(f"  Letra sincronizada (SYLT/LRC): {synced_count}")
    print(f"  Letra plana (USLT sin ts):     {plain_count}")
    print(f"  Sin letra:                     {none_count}")
    print()

    return 1 if fail_count > 0 else 0


# ── REPAIR ─────────────────────────────────────────────────────────

def _embed_synced_lyrics(audio, lrc_text: str, lang: str = "eng") -> bool:
    """Embed synced lyrics into an audio file. Returns True on success."""
    try:
        entries = parse_lrc(lrc_text)
        if not entries:
            return False

        plain_lines = [txt for _, txt in entries]

        if isinstance(audio, MP3):
            if audio.tags is None:
                audio.add_tags()
            audio.tags.delall("USLT:")
            audio.tags.delall("SYLT:")
            # SYLT
            synced_parts = [
                (txt, ms) for ms, txt in entries if txt.strip()
            ]
            if synced_parts:
                audio.tags.add(
                    SYLT(
                        encoding=3,
                        lang=lang,
                        format=2,
                        type=1,
                        text=synced_parts,
                    )
                )
            # USLT (plain)
            if plain_lines:
                audio.tags.add(
                    USLT(encoding=3, lang=lang, text=plain_lines)
                )

        elif isinstance(audio, FLAC):
            normalised = normalise_lrc(entries)
            audio["LYRICS"] = normalised.split("\n")
            audio["SYNCEDLYRICS"] = [normalised]

        elif isinstance(audio, MP4):
            normalised = normalise_lrc(entries)
            audio["\xa9lyr"] = [normalised]
            audio[ITUNES_SYNCED_ATOM] = [normalised.encode("utf-8")]

        audio.save()
        return True
    except Exception:
        return False


def _embed_plain_lyrics(audio, text: str, lang: str = "eng") -> bool:
    """Embed plain lyrics into an audio file."""
    try:
        if isinstance(audio, MP3):
            if audio.tags is None:
                audio.add_tags()
            audio.tags.delall("USLT:")
            lines = [l for l in text.split("\n") if l.strip()]
            if lines:
                audio.tags.add(USLT(encoding=3, lang=lang, text=lines))
        elif isinstance(audio, FLAC):
            lines = [l for l in text.split("\n") if l.strip()]
            if lines:
                audio["LYRICS"] = lines
        elif isinstance(audio, MP4):
            audio["\xa9lyr"] = [text]
        audio.save()
        return True
    except Exception:
        return False


def _lang_iso3(lang: str) -> str:
    """Convert a 2-letter ISO 639-1 code to 3-letter ISO 639-2 for ID3."""
    mapping = {
        "en": "eng", "es": "spa", "pt": "por", "fr": "fre",
        "de": "ger", "it": "ita", "ja": "jpn", "ko": "kor",
        "zh": "chi", "ru": "rus", "ar": "ara", "nl": "dut",
        "pl": "pol", "sv": "swe", "tr": "tur", "he": "heb",
    }
    if not lang:
        return "eng"
    code = lang.lower()[:2]
    return mapping.get(code, "eng")


def repair_track(
    filepath: Path,
    lang: str = "",
    dry_run: bool = False,
) -> TrackVerdict:
    """Repair lyrics for one track: re-embed from sidecar or fix state.

    Returns the post-repair verdict (re-read from disk).
    """
    audio = _open_audio(str(filepath))
    sidecar = filepath.with_suffix(".lrc")
    iso_lang = _lang_iso3(lang)

    if audio is None:
        return TrackVerdict(
            file=str(filepath), status="FAIL",
            issues=["no se pudo abrir el archivo"],
        )

    if dry_run:
        # Dry run: just report what would be done
        v = verify_track(filepath)
        if v.status != "OK":
            if sidecar.is_file():
                v.issues.append("[dry-run] reincrustaría desde sidecar .lrc")
            else:
                v.issues.append("[dry-run] necesita re-búsqueda de letra")
        return v

    # ── Actual repair ──────────────────────────────────────────────
    # Backup
    bak = filepath.with_suffix(filepath.suffix + ".bak")
    try:
        shutil.copy2(str(filepath), str(bak))
    except Exception:
        return TrackVerdict(
            file=str(filepath), status="FAIL",
            issues=["no se pudo crear backup"],
        )

    repaired = False

    if sidecar.is_file():
        # Re-embed from sidecar
        try:
            sidecar_text = sidecar.read_text(encoding="utf-8", errors="replace")
            if _is_lrc_text(sidecar_text):
                if _embed_synced_lyrics(audio, sidecar_text, iso_lang):
                    repaired = True
            else:
                if _embed_plain_lyrics(audio, sidecar_text, iso_lang):
                    repaired = True
        except Exception:
            pass

    if repaired:
        # Write sidecar from embedded (atomic: temp + rename)
        try:
            synced, _ = _extract_embedded_lyrics(audio)
            if synced and _is_lrc_text(synced):
                fd, tmp_path = tempfile.mkstemp(
                    suffix=".lrc.tmp",
                    dir=str(filepath.parent),
                    prefix=".lrc_",
                )
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        f.write(synced)
                    Path(tmp_path).rename(sidecar)
                except Exception:
                    os.close(fd)
                    if Path(tmp_path).exists():
                        Path(tmp_path).unlink()
        except Exception:
            pass

    # Remove backup on success
    if bak.exists():
        bak.unlink()

    # Re-read and validate
    return verify_track(filepath)


def run_lyrics_repair(args) -> int:
    """lyrics repair <dir> [--dry-run] [--lang LANG]"""
    target = Path(args.path)
    dry_run = args.dry_run
    lang = getattr(args, "lang", "") or ""
    iso_lang = _lang_iso3(lang)

    if not target.exists():
        print(f"Ruta no encontrada: {args.path}", file=sys.stderr)
        return 1

    files = sorted(
        f for f in target.rglob("*")
        if f.is_file() and f.suffix.lower() in AUDIO_EXTS
    )
    if not files:
        print("No se encontraron archivos de audio.")
        return 0

    mode_label = "SIMULACIÓN (no se modifica nada)" if dry_run else "REPARACIÓN"
    print()
    print(f"  ── {mode_label} de letras: {target} ──")
    if iso_lang and lang:
        print(f"  Idioma: {lang} (ISO-3: {iso_lang})")
    print()

    ok_count = 0
    warn_count = 0
    fail_count = 0
    synced_count = 0
    plain_count = 0
    none_count = 0

    for f in files:
        v = repair_track(f, lang=lang, dry_run=dry_run)
        icon = {"OK": "✔", "WARN": "⚠", "FAIL": "✘"}.get(v.status, "?")
        print(f"  {icon} [{v.status}] {f.name}")
        if v.issues:
            for issue in v.issues:
                print(f"      • {issue}")

        if v.status == "OK":
            ok_count += 1
        elif v.status == "WARN":
            warn_count += 1
        else:
            fail_count += 1

        if v.kind == "synced":
            synced_count += 1
        elif v.kind == "plain":
            plain_count += 1
        else:
            none_count += 1

    total = len(files)
    print()
    print(f"  Total: {total}")
    print(f"  OK:    {ok_count}")
    print(f"  WARN:  {warn_count}")
    print(f"  FAIL:  {fail_count}")
    print()
    print(f"  ── Contadores ──")
    print(f"  Letra sincronizada (SYLT/LRC): {synced_count}")
    print(f"  Letra plana (USLT sin ts):     {plain_count}")
    print(f"  Sin letra:                     {none_count}")
    print()

    return 1 if fail_count > 0 and not dry_run else 0


# ── SHIFT (enhanced: tag + sidecar) ────────────────────────────────

def run_lyrics_shift(args) -> int:
    """lyrics shift <file> <ms> — adjust offset on tag AND sidecar."""
    filepath = Path(args.file)
    ms_shift = args.ms

    if not filepath.is_file():
        print(f"Archivo no encontrado: {args.file}", file=sys.stderr)
        return 1

    audio = _open_audio(str(filepath))
    sidecar = filepath.with_suffix(".lrc")

    # Collect LRC text from either source
    lrc_text = None
    source = ""

    if audio:
        synced, _ = _extract_embedded_lyrics(audio)
        if synced and _is_lrc_text(synced):
            lrc_text = synced
            source = "tag"

    if not lrc_text and sidecar.is_file():
        try:
            lrc_text = sidecar.read_text(encoding="utf-8", errors="replace")
            if _is_lrc_text(lrc_text):
                source = "sidecar"
        except Exception:
            pass

    if not lrc_text:
        print("No se encontraron letras sincronizadas.")
        return 1

    # Shift
    shifted = _shift_lrc_text(lrc_text, ms_shift)

    # Write sidecar (atomic)
    if sidecar.is_file() or source:
        try:
            fd, tmp_path = tempfile.mkstemp(
                suffix=".lrc.tmp",
                dir=str(filepath.parent),
                prefix=".lrc_",
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(shifted)
                Path(tmp_path).rename(sidecar)
            except Exception:
                os.close(fd)
                if Path(tmp_path).exists():
                    Path(tmp_path).unlink()
            print(f"  Sidecar actualizado: {sidecar.name} ({ms_shift}ms)")
        except Exception as e:
            print(f"  Error al escribir sidecar: {e}", file=sys.stderr)

    # Re-embed into tag
    if audio and _is_lrc_text(shifted):
        lang = getattr(args, "lang", "") or ""
        iso_lang = _lang_iso3(lang)
        if _embed_synced_lyrics(audio, shifted, iso_lang):
            print(f"  Tag actualizado: {filepath.name} ({ms_shift}ms)")
        else:
            print(f"  ⚠ Error al re-incrustar tag en {filepath.name}")

    # Verify post-write
    if audio:
        audio2 = _open_audio(str(filepath))
        if audio2:
            synced2, _ = _extract_embedded_lyrics(audio2)
            if synced2 and _is_lrc_text(synced2):
                entries = parse_lrc(synced2)
                if entries:
                    print(f"  ✔ Verificación post-escritura: {len(entries)} líneas OK")
                else:
                    print("  ✘ Verificación post-escritura: timestamps inválidos")
            else:
                print("  ⚠ Verificación post-escritura: no se leyeron timestamps")

    return 0


# ── Dispatcher ─────────────────────────────────────────────────────

def run_lyrics_manage(args) -> int:
    """Dispatch lyrics-manage subcommands (verify / repair / shift)."""
    cmd = getattr(args, "lyrics_manage_cmd", None)
    if cmd == "verify":
        return run_lyrics_verify(args)
    elif cmd == "repair":
        return run_lyrics_repair(args)
    elif cmd == "shift":
        return run_lyrics_shift(args)
    else:
        print("Usage: lyrics-manage {verify|repair|shift}")
        return 1
