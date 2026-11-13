#!/usr/bin/env python3
"""Lyrics utility commands (lyrics-tool subcommand)."""
from __future__ import annotations

import re
from pathlib import Path

try:
    import mutagen
    from mutagen.mp3 import MP3
    from mutagen.flac import FLAC
    from mutagen.mp4 import MP4
    from mutagen.id3 import ID3, USLT, SYLT
    from mutagen.id3 import ID3NoHeaderError
    HAS_MUTAGEN = True
except ImportError:
    HAS_MUTAGEN = False


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


def _get_embedded_lyrics(audio) -> str | None:
    """Extract embedded lyrics from an audio file.

    Priority: TXXX:LYRICS (PowerAmp synced) > USLT (plain) > FLAC LYRICS > MP4 ©lyr.
    """
    if audio is None:
        return None
    try:
        if isinstance(audio, MP3) and audio.tags:
            # TXXX:LYRICS (PowerAmp synced) — check first
            txxx = audio.tags.getall("TXXX:LYRICS")
            if txxx:
                text = "\n".join(str(t) for t in txxx[0].text)
                if text.strip():
                    return text
            # USLT (plain) — fallback
            for uslt in audio.tags.getall("USLT:"):
                return str(uslt.text)
        elif isinstance(audio, FLAC) and audio.tags:
            lyrics = audio.get("LYRICS")
            if lyrics:
                return "\n".join(lyrics)
        elif isinstance(audio, MP4) and audio.tags:
            lyr = audio.get("\xa9lyr")
            if lyr:
                return str(lyr[0])
    except Exception:
        pass
    return None


def _parse_lrc(text: str) -> list[tuple[float, str]]:
    """Parse LRC format into list of (seconds, text)."""
    entries = []
    pat = re.compile(r"\[(\d{2}):(\d{2})(?:\.(\d{2,3}))?\]\s*(.*)")
    for line in text.splitlines():
        m = pat.match(line.strip())
        if m:
            mins, secs = int(m.group(1)), int(m.group(2))
            ms = m.group(3) or "0"
            if len(ms) == 2:
                ms += "0"
            time_s = mins * 60 + secs + int(ms) / 1000.0
            entries.append((time_s, m.group(4)))
    return entries


def _format_lrc(entries: list[tuple[float, str]]) -> str:
    """Format entries as LRC string (2-digit centiseconds for compatibility)."""
    lines = []
    for ts, text in entries:
        mins = int(ts // 60)
        secs = int(ts % 60)
        ms = int((ts % 1) * 1000)
        lines.append(f"[{mins:02d}:{secs:02d}.{ms // 10:02d}] {text}")
    return "\n".join(lines)


def _embed_lyrics_text(audio, text: str) -> None:
    """Embed lyrics text into audio file.

    If the text contains LRC timestamps, also writes TXXX:LYRICS
    so that PowerAmp can read synced lyrics from MP3 files.
    """
    if audio is None:
        return
    try:
        if isinstance(audio, MP3):
            if audio.tags is None:
                audio.add_tags()
            audio.tags.delall("USLT:")
            audio.tags.add(USLT(encoding=3, lang="eng", text=text.split("\n")))
            # If text looks like LRC, also write TXXX:LYRICS for PowerAmp
            if any(l.strip().startswith("[") for l in text.split("\n") if l.strip()):
                audio.tags.delall("TXXX:LYRICS")
                from mutagen.id3 import TXXX
                audio.tags.add(TXXX(encoding=3, desc="LYRICS", text=text))
        elif isinstance(audio, FLAC):
            audio["LYRICS"] = text.split("\n")
    except Exception:
        pass


def run_lyrics_tool(args) -> int:
    """Dispatch lyrics-tool subcommands."""
    cmd = getattr(args, "lyrics_tool_cmd", None)

    if cmd == "show":
        target = args.target
        audio = _get_audio_file(target)
        lyrics = _get_embedded_lyrics(audio) if audio else None
        if lyrics:
            print(lyrics)
        else:
            # Check for .lrc file
            lrc_path = Path(target).with_suffix(".lrc")
            if lrc_path.is_file():
                print(lrc_path.read_text(encoding="utf-8", errors="replace"))
            else:
                print("  No se encontraron letras.")
        return 0

    if cmd == "compare":
        target = args.target
        audio = _get_audio_file(target)
        lyrics = _get_embedded_lyrics(audio) if audio else None
        print(f"  Archivo: {target}")
        if lyrics:
            lines = [l for l in lyrics.split("\n") if l.strip()]
            print(f"  Letras incrustadas: {len(lines)} líneas")
            if any(l.startswith("[") for l in lines):
                print("  Tipo: sincronizadas (LRC)")
            else:
                print("  Tipo: texto plano")
        else:
            print("  Sin letras incrustadas")
        lrc_path = Path(target).with_suffix(".lrc")
        if lrc_path.is_file():
            print("  Archivo .lrc: sí")
        else:
            print("  Archivo .lrc: no")
        return 0

    if cmd == "shift":
        target = args.target
        ms_shift = args.ms
        # Try .lrc file first
        lrc_path = Path(target).with_suffix(".lrc") if not target.endswith(".lrc") else Path(target)
        if not lrc_path.is_file():
            # Try extracting from audio
            audio = _get_audio_file(target.replace(".lrc", "")) if target.endswith(".lrc") else _get_audio_file(target)
            lyrics = _get_embedded_lyrics(audio) if audio else None
            if lyrics and any(l.startswith("[") for l in lyrics.split("\n")):
                entries = _parse_lrc(lyrics)
            else:
                print("  No se encontraron letras sincronizadas.")
                return 1
        else:
            text = lrc_path.read_text(encoding="utf-8", errors="replace")
            entries = _parse_lrc(text)

        if not entries:
            print("  No se encontraron marcas temporales.")
            return 1

        shift_s = ms_shift / 1000.0
        new_entries = [
            (max(0, ts + shift_s), txt) for ts, txt in entries
        ]
        new_text = _format_lrc(new_entries)

        if target.endswith(".lrc") or lrc_path.suffix == ".lrc":
            lrc_path.write_text(new_text, encoding="utf-8")
            print(f"  Desfase aplicado: {ms_shift}ms → {lrc_path}")
        else:
            # Save to .lrc file
            out = Path(target).with_suffix(".lrc")
            out.write_text(new_text, encoding="utf-8")
            print(f"  Desfase aplicado: {ms_shift}ms → {out}")

        return 0

    if cmd == "convert":
        target = args.target
        if not Path(target).is_file():
            print(f"Archivo no encontrado: {target}")
            return 1
        text = Path(target).read_text(encoding="utf-8", errors="replace")
        entries = _parse_lrc(text)
        if not entries:
            # Already plain text
            lines = [l.strip() for l in text.split("\n") if l.strip() and not l.strip().startswith("[")]
            plain = "\n".join(lines)
        else:
            plain = "\n".join(txt for _, txt in entries)
        out = Path(target).with_suffix(".txt")
        out.write_text(plain, encoding="utf-8")
        print(f"  Convertido: {target} → {out}")
        return 0

    if cmd == "set":
        target = args.target
        source = args.source
        if not Path(source).is_file():
            print(f"Archivo de letra no encontrado: {source}")
            return 1
        text = Path(source).read_text(encoding="utf-8", errors="replace")
        audio = _get_audio_file(target)
        if audio is None:
            print(f"No se pudo abrir: {target}")
            return 1
        _embed_lyrics_text(audio, text)
        audio.save()
        print(f"  Letra incrustada: {source} → {target}")
        return 0

    print("Usage: lyrics-tool {show|compare|shift|convert|set}")
    return 1
