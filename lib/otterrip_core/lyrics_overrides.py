#!/usr/bin/env python3
"""Lyrics overrides persistence and interactive adjustment tools.

Stores per-track offset overrides and selected provider results in a JSON
file under ~/.config/otterrip/overrides.json so the user can fine-tune
lyrics timing without editing files manually.

Data format:
{
    "/absolute/path/to/audio.mp3": {
        "offset_ms": 1240,
        "provider": "lrclib",
        "lrc_hash": "a3f2b8c1d4e5f6a7",
        "selected_at": "2026-11-18T13:00:00"
    }
}

Usage from Python:
    from otterrip_core.lyrics_overrides import OverridesStore
    store = OverridesStore()
    store.set_offset("/path/to/audio.mp3", 1240)
    offset = store.get_offset("/path/to/audio.mp3")  # 1240

Usage from CLI:
    python -m otterrip_core lyrics-overrides get /path/to/audio.mp3
    python -m otterrip_core lyrics-overrides set /path/to/audio.mp3 --offset 1240
    python -m otterrip_core lyrics-overrides interactive /path/to/audio.mp3
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Default config directory
DEFAULT_CONFIG_DIR = Path.home() / ".config" / "otterrip"
OVERRIDES_FILE = "overrides.json"


def _config_dir() -> Path:
    """Return the config directory, creating it if needed."""
    d = DEFAULT_CONFIG_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _overrides_path() -> Path:
    """Return the path to the overrides JSON file."""
    return _config_dir() / OVERRIDES_FILE


def _normalize_path(filepath: str) -> str:
    """Normalize a file path for consistent key lookup."""
    return str(Path(filepath).resolve())


class OverridesStore:
    """Persistent store for per-track lyrics overrides.

    Stores offset_ms, provider selection, and LRC hash for each audio file.
    """

    def __init__(self, config_dir: Optional[Path] = None):
        self.config_dir = config_dir or _config_dir()
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.config_dir / OVERRIDES_FILE
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        """Load overrides from disk, returning empty dict if not found."""
        if not self.path.is_file():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("overrides.json corrupt, resetting: %s", exc)
            return {}

    def _save(self) -> None:
        """Persist overrides to disk."""
        self.path.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def get(self, filepath: str) -> Optional[dict[str, Any]]:
        """Get all overrides for a file."""
        key = _normalize_path(filepath)
        return self._data.get(key)

    def get_offset(self, filepath: str) -> int:
        """Get the stored offset in milliseconds for a file, or 0 if not set."""
        entry = self.get(filepath)
        if entry is None:
            return 0
        return int(entry.get("offset_ms", 0))

    def set_offset(self, filepath: str, offset_ms: int) -> None:
        """Set the offset override for a file."""
        key = _normalize_path(filepath)
        if key not in self._data:
            self._data[key] = {}
        self._data[key]["offset_ms"] = offset_ms
        self._save()

    def get_provider(self, filepath: str) -> Optional[str]:
        """Get the stored provider selection for a file."""
        entry = self.get(filepath)
        if entry is None:
            return None
        return entry.get("provider")

    def set_provider(self, filepath: str, provider: str, lrc_hash: str = "") -> None:
        """Set the provider selection for a file."""
        import datetime
        key = _normalize_path(filepath)
        if key not in self._data:
            self._data[key] = {}
        self._data[key]["provider"] = provider
        if lrc_hash:
            self._data[key]["lrc_hash"] = lrc_hash
        self._data[key]["selected_at"] = datetime.datetime.now().isoformat()
        self._save()

    def clear(self, filepath: str) -> None:
        """Clear all overrides for a file."""
        key = _normalize_path(filepath)
        if key in self._data:
            del self._data[key]
            self._save()

    def list_overrides(self) -> list[dict[str, Any]]:
        """List all stored overrides."""
        results = []
        for filepath, overrides in self._data.items():
            results.append({
                "file": filepath,
                "offset_ms": overrides.get("offset_ms", 0),
                "provider": overrides.get("provider"),
                "lrc_hash": overrides.get("lrc_hash", ""),
                "selected_at": overrides.get("selected_at", ""),
            })
        return results


def compute_lrc_hash(lrc_text: str) -> str:
    """Compute a short hash of LRC text for change detection."""
    return hashlib.sha256(lrc_text.encode("utf-8")).hexdigest()[:16]


def apply_offset_to_lrc(lrc_text: str, offset_ms: int) -> str:
    """Apply a time offset to all timestamps in LRC text.

    Args:
        lrc_text: LRC formatted text with [mm:ss.xx] timestamps.
        offset_ms: Offset in milliseconds (positive = delay, negative = advance).

    Returns:
        Modified LRC text with adjusted timestamps.
    """
    import re

    offset_s = offset_ms / 1000.0
    lines = lrc_text.split("\n")
    result = []
    pat = re.compile(r"(\[(\d{2}):(\d{2})\.(\d{2,3})\])")

    for line in lines:
        m = pat.search(line)
        if m:
            mins = int(m.group(2))
            secs = int(m.group(3))
            ms_str = m.group(4)
            if len(ms_str) == 2:
                ms_str += "0"
            ms = int(ms_str)
            total_s = mins * 60 + secs + ms / 1000.0
            new_s = max(0, total_s + offset_s)
            new_mins = int(new_s // 60)
            new_secs = int(new_s % 60)
            new_ms = int((new_s % 1) * 1000)
            # Use 2-digit centiseconds for compatibility
            new_tag = f"[{new_mins:02d}:{new_secs:02d}.{new_ms // 10:02d}]"
            line = line.replace(m.group(1), new_tag, 1)
        result.append(line)

    return "\n".join(result)


# ── Interactive CLI helpers ────────────────────────────────────────

def interactive_offset_adjust(audio_path: str) -> int:
    """Run an interactive offset adjustment session.

    Displays the first few lines of the LRC, lets the user press +/-
    to adjust in 100ms steps, and saves the result.

    Args:
        audio_path: Path to the audio file (or .lrc sidecar).

    Returns:
        The final offset in milliseconds, or 0 if cancelled.
    """
    from pathlib import Path

    # Find LRC content
    p = Path(audio_path)
    lrc_path = p if p.suffix.lower() == ".lrc" else p.with_suffix(".lrc")
    lrc_text = ""

    if lrc_path.is_file():
        lrc_text = lrc_path.read_text(encoding="utf-8", errors="replace")
    else:
        # Try embedded lyrics
        try:
            from otterrip_core.lyrics_tool import (
                _get_audio_file,
                _get_embedded_lyrics,
            )
            audio = _get_audio_file(str(p))
            if audio:
                lyrics = _get_embedded_lyrics(audio)
                if lyrics:
                    lrc_text = lyrics
        except Exception:
            pass

    if not lrc_text:
        print("  No se encontraron letras sincronizadas.")
        return 0

    # Load existing offset
    store = OverridesStore()
    base_offset = store.get_offset(audio_path)
    current_offset = base_offset

    # Parse first few lines for display
    import re
    pat = re.compile(r"\[(\d{2}):(\d{2})(?:\.(\d{2,3}))?\]\s*(.*)")
    lines = []
    for line in lrc_text.split("\n")[:10]:
        m = pat.match(line.strip())
        if m:
            lines.append(line.strip())

    if not lines:
        print("  No se encontraron marcas temporales.")
        return 0

    print()
    print("  ═══ Ajuste interactivo de offset ═══")
    print(f"  Archivo: {audio_path}")
    print(f"  Offset actual: {base_offset:+d} ms")
    print()
    print("  Controles:")
    print("    + / p  : +100 ms (retrasar)")
    print("    - / o  : -100 ms (adelantar)")
    print("    P / +  : +1000 ms")
    print("    O / -  : -1000 ms")
    print("    s / S  : guardar y salir")
    print("    e / q  : cancelar (sin guardar)")
    print()

    while True:
        # Display preview with current offset applied
        preview_text = apply_offset_to_lrc(lrc_text, current_offset)
        preview_lines = []
        for line in preview_text.split("\n")[:5]:
            if line.strip():
                preview_lines.append(line.strip())

        offset_display = f"{current_offset - base_offset:+d}" if current_offset != base_offset else "+0"
        print(f"  Offset: {offset_display} ms  |  Preview:")
        for pl in preview_lines:
            print(f"    {pl}")
        print()

        try:
            key = input("  Pulsa una tecla: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  Cancelado.")
            return 0

        if key in ("s",):
            if current_offset != base_offset:
                store.set_offset(audio_path, current_offset)
                print(f"  Offset guardado: {current_offset} ms")
            else:
                print("  Sin cambios.")
            return current_offset
        elif key in ("e", "q"):
            print("  Cancelado.")
            return 0
        elif key in ("+", "p"):
            current_offset += 100
        elif key in ("-", "o"):
            current_offset -= 100
        elif key == "P" or key == "++":
            current_offset += 1000
        elif key == "O" or key == "--":
            current_offset -= 1000
        else:
            print("  Tecla no reconocida.")
        print()


def list_candidates_for_selection(
    audio_path: str,
    candidates: list[dict[str, Any]],
) -> Optional[int]:
    """Display a list of lyric candidates and let the user choose.

    Args:
        audio_path: Path to the audio file.
        candidates: List of candidate dicts with keys:
            - provider: str (e.g. "lrclib", "musixmatch")
            - sync_level: str (e.g. "synced", "plain")
            - title: str
            - artist: str
            - preview: str (first 3 lines of lyrics)
            - score: float (0.0-1.0)

    Returns:
        The index of the selected candidate (0-based), or None if cancelled.
    """
    if not candidates:
        print("  No hay candidatos disponibles.")
        return None

    print()
    print("  ═══ Selector de resultados de letras ═══")
    print(f"  Archivo: {audio_path}")
    print(f"  {len(candidates)} candidatos encontrados:")
    print()

    for i, c in enumerate(candidates):
        provider = c.get("provider", "desconocido")
        sync = c.get("sync_level", "plain")
        title = c.get("title", "")
        artist = c.get("artist", "")
        score = c.get("score", 0.0)
        preview = c.get("preview", "")

        sync_label = "🎵 sync" if sync == "synced" else "📝 texto"
        print(f"  {i + 1}. [{provider}] {sync_label} (score: {score:.2f})")
        if title:
            print(f"     {title} - {artist}")
        if preview:
            for line in preview.split("\n")[:3]:
                if line.strip():
                    print(f"     │ {line.strip()}")
        print()

    try:
        choice = input(
            f"  Elige un candidato (1-{len(candidates)}) o 'e' para cancelar: "
        ).strip()
    except (EOFError, KeyboardInterrupt):
        print("\n  Cancelado.")
        return None

    if choice.lower() in ("e", "q"):
        return None

    try:
        idx = int(choice) - 1
        if 0 <= idx < len(candidates):
            return idx
        else:
            print(f"  Índice inválido: {choice}")
            return None
    except ValueError:
        print(f"  Entrada inválida: {choice}")
        return None


def save_selected_candidate(
    audio_path: str,
    candidate: dict[str, Any],
) -> None:
    """Save the user's candidate selection to the overrides store."""
    store = OverridesStore()
    provider = candidate.get("provider", "desconocido")
    lrc_text = candidate.get("lrc_text", "")
    lrc_hash = compute_lrc_hash(lrc_text) if lrc_text else ""
    store.set_provider(audio_path, provider, lrc_hash)
    print(f"  Candidato guardado: {provider} (hash: {lrc_hash})")


# ── CLI entry point ────────────────────────────────────────────────

def run_lyrics_overrides(args: Any) -> int:
    """Dispatch lyrics-overrides subcommands."""
    cmd = getattr(args, "lyrics_overrides_cmd", None)

    if cmd == "get":
        store = OverridesStore()
        offset = store.get_offset(args.file)
        entry = store.get(args.file)
        print(f"  Archivo: {args.file}")
        print(f"  Offset: {offset:+d} ms")
        if entry and entry.get("provider"):
            print(f"  Proveedor: {entry['provider']}")
        if entry and entry.get("lrc_hash"):
            print(f"  LRC hash: {entry['lrc_hash']}")
        return 0

    if cmd == "set":
        store = OverridesStore()
        store.set_offset(args.file, args.offset)
        print(f"  Offset guardado: {args.file} → {args.offset:+d} ms")
        return 0

    if cmd == "interactive":
        result = interactive_offset_adjust(args.file)
        if result:
            print(f"  Offset final: {result:+d} ms")
        return 0

    if cmd == "select":
        # Search for lyrics and present candidates
        import asyncio
        from otterrip_core.lyrics import (
            _build_query,
            _build_providers,
            _search_one,
        )
        from otterrip_core.adapters.base import SongQuery
        from otterrip_core.lyrics_bundle import LyricsBundle

        # Build query from audio file
        try:
            from otterrip_core.lyrics_tool import _get_audio_file, _get_embedded_lyrics
            from mutagen.mp3 import MP3
            from mutagen.flac import FLAC
            from mutagen.mp4 import MP4
            import re as _re

            audio = _get_audio_file(args.file)
            if audio is None:
                print("  No se pudo abrir el archivo de audio.")
                return 1

            # Extract basic metadata
            title = ""
            artist = ""
            if isinstance(audio, MP3) and audio.tags:
                for tag in audio.tags.getall("TIT2"):
                    title = str(tag.text[0]) if tag.text else ""
                for tag in audio.tags.getall("TPE1"):
                    artist = str(tag.text[0]) if tag.text else ""
            elif isinstance(audio, FLAC):
                title = audio.get("TITLE", [""])[0]
                artist = audio.get("ARTIST", [""])[0]
            elif isinstance(audio, MP4):
                title = str(audio.get("\xa9nam", [""])[0])
                artist = str(audio.get("\xa9ART", [""])[0])

            if not title or not artist:
                print("  No se encontraron metadatos de título/artista.")
                return 1

        except Exception as exc:
            print(f"  Error al leer metadatos: {exc}")
            return 1

        # Build providers
        providers_str = getattr(args, "providers", "lrclib,multiweb")
        providers = _build_providers(
            providers_str,
            host=getattr(args, "host", "https://lrclib.net"),
            synced_dir="",
            lang=getattr(args, "lang", ""),
        )

        query = SongQuery(artist=artist, title=title)

        # Search all providers and collect candidates
        print(f"  Buscando letras para: {title} - {artist}")
        print()

        candidates = []
        for provider in providers:
            try:
                bundles = asyncio.get_event_loop().run_until_complete(
                    _search_one(provider, query, plain_ok=True)
                )
                for bundle in bundles:
                    # Get preview lines
                    preview_lines = []
                    if bundle.lrc:
                        pat = _re.compile(r"\[(\d{2}):(\d{2})(?:\.(\d{2,3}))?\]\s*(.*)")
                        for line in bundle.lrc.split("\n")[:3]:
                            m = pat.match(line.strip())
                            if m:
                                preview_lines.append(m.group(4))
                            elif line.strip():
                                preview_lines.append(line.strip())
                    elif bundle.plain:
                        for line in bundle.plain.split("\n")[:3]:
                            if line.strip():
                                preview_lines.append(line.strip())

                    candidates.append({
                        "provider": provider.name,
                        "sync_level": "synced" if bundle.lrc else "plain",
                        "title": bundle.title or title,
                        "artist": bundle.artist or artist,
                        "preview": "\n".join(preview_lines),
                        "score": bundle.confidence or 0.0,
                        "lrc_text": bundle.lrc or bundle.plain or "",
                    })
            except Exception:
                pass

            # Limit to 10 candidates
            if len(candidates) >= 10:
                break

        if not candidates:
            print("  No se encontraron candidatos.")
            return 1

        idx = list_candidates_for_selection(args.file, candidates)
        if idx is not None:
            save_selected_candidate(args.file, candidates[idx])
            # Also apply the selected lyrics
            selected = candidates[idx]
            lrc_text = selected.get("lrc_text", "")
            if lrc_text:
                from pathlib import Path
                lrc_path = Path(args.file).with_suffix(".lrc")
                lrc_path.write_text(lrc_text, encoding="utf-8")
                print(f"  Letra guardada en: {lrc_path}")
        else:
            print("  Cancelado.")
        return 0

    if cmd == "list":
        store = OverridesStore()
        overrides = store.list_overrides()
        if not overrides:
            print("  No hay overrides almacenados.")
            return 0
        print()
        print("  ═══ Overrides almacenados ═══")
        for ov in overrides:
            print(f"  Archivo: {ov['file']}")
            print(f"    Offset: {ov['offset_ms']:+d} ms")
            if ov.get("provider"):
                print(f"    Proveedor: {ov['provider']}")
            if ov.get("selected_at"):
                print(f"    Seleccionado: {ov['selected_at']}")
            print()
        return 0

    print("Usage: lyrics-overrides {get|set|interactive|select|list}")
    return 1
