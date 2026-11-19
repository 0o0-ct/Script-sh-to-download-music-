#!/usr/bin/env python3
"""Clean YouTube junk from downloaded filenames and rewrite manifests.

The yt-dlp output template is ``%(title)s [%(id)s].%(ext)s``, so every
downloaded file carries YouTube noise: ``[Official Music Video]``,
``[4K Remaster]``, the video id ``[NdYWuo9OFAw]`` …  This module renames
those files to a clean ``Artista - Canción.ext`` form using
:func:`otterrip_core.query_clean.clean_title`, and applies the very same
rename to any JSON-line session manifest that references the old paths
(keys ``filepath`` or ``file``), so the verification / lyrics pipeline
keeps working after the rename.

Note: a single ``Artista - Canción`` prefix is intentionally KEPT (that is
the desired final name).  Only a repeated artist prefix (``Artista -
Artista - Canción``, from YouTube Music) is removed — that happens inside
``clean_title``.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from otterrip_core.query_clean import clean_title

# Extensions eligible for renaming: audio files plus side-cars yt-dlp may
# leave behind (cover art, subtitles, converted lyrics).
AUDIO_EXTS = {".mp3", ".m4a", ".flac", ".webm", ".mkv", ".mp4"}
SIDECAR_EXTS = {".lrc", ".jpg", ".webp", ".png", ".vtt", ".srt"}
ALLOWED_EXTS = AUDIO_EXTS | SIDECAR_EXTS

# Compound subtitle extensions: "<base>.<lang>.<container>", e.g. ".en.lrc",
# ".es.vtt", ".es-419.lrc".
_COMPOUND_EXT_RE = re.compile(
    r"^(?P<lang>[A-Za-z]{2}(?:[-_][A-Za-z0-9]{2,8})?)\.(?P<container>lrc|vtt|srt)$",
    re.IGNORECASE,
)

# Manifest JSON keys that hold a file path.
_PATH_KEYS = ("filepath", "file")


def split_ext(name: str) -> tuple[str, str]:
    """Split a filename into (base, extension), honouring compound forms.

    Returns ("Song [Official]", ".en.lrc") for "Song [Official].en.lrc".
    """
    stem, ext = os.path.splitext(name)
    if ext and stem:
        inner_stem, inner_ext = os.path.splitext(stem)
        if inner_ext.startswith("."):
            compound = inner_ext + ext
            if _COMPOUND_EXT_RE.match(compound.lstrip(".")):
                return inner_stem, compound
    return stem, ext


def container_ext(ext: str) -> str:
    """Last container of a (possibly compound) extension.

    ".en.lrc" → ".lrc"; ".m4a" → ".m4a"; "" → "".
    """
    if not ext:
        return ""
    return "." + ext.lstrip(".").rsplit(".", 1)[-1]


def plan_name(base: str) -> str:
    """Return the cleaned base name (without extension) for a file stem."""
    if not base:
        return ""
    new_base = clean_title(base)
    return new_base.strip(" -–—_|.")


def _read_manifest_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []


def _write_manifest_lines(path: Path, lines: list[str]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass


def _rewrite_manifest(path: Path, mapping: dict[str, str]) -> bool:
    """Apply *mapping* (old abs path → new abs path) to a JSON-line manifest.

    Only JSON lines whose ``filepath``/``file`` value changes are rewritten;
    everything else (raw ids, non-JSON lines) is preserved verbatim.
    """
    lines = _read_manifest_lines(path)
    changed = False
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        replaced = False
        if stripped.startswith("{"):
            try:
                info = json.loads(stripped)
            except json.JSONDecodeError:
                info = None
            if isinstance(info, dict):
                for key in _PATH_KEYS:
                    value = info.get(key)
                    if isinstance(value, str) and value:
                        new = mapping.get(os.path.abspath(value))
                        if new and new != value:
                            info[key] = new
                            out.append(json.dumps(info, ensure_ascii=True))
                            changed = True
                            replaced = True
                        break
        if not replaced:
            out.append(line)
    if changed:
        _write_manifest_lines(path, out)
    return changed


def clean_and_rename(
    dest: str,
    manifests: list[str] | None = None,
    dry_run: bool = False,
) -> int:
    """Clean filenames under *dest* and rewrite *manifests* to match.

    Returns the number of files renamed.
    """
    try:
        dest_path = Path(dest).resolve()
    except OSError:
        return 0
    if not dest_path.is_dir():
        return 0

    manifest_paths = [Path(raw) for raw in (manifests or []) if raw and Path(raw).is_file()]

    mapping: dict[str, str] = {}
    renamed = 0
    for root, _dirs, files in os.walk(dest_path):
        for name in sorted(files):
            fpath = os.path.join(root, name)
            base, ext = split_ext(name)
            if container_ext(ext).lower() not in ALLOWED_EXTS:
                continue
            new_base = plan_name(base)
            if not new_base or new_base == base:
                continue
            new_name = new_base + ext
            new_path = os.path.join(root, new_name)
            if os.path.lexists(new_path):
                # Target already exists (same clean name): skip to avoid
                # overwriting a different track.
                continue
            if not dry_run:
                try:
                    os.rename(fpath, new_path)
                except OSError:
                    continue
            mapping[os.path.abspath(fpath)] = os.path.abspath(new_path)
            renamed += 1

    if mapping and not dry_run:
        for m in manifest_paths:
            _rewrite_manifest(m, mapping)

    return renamed


def run_clean_names(args) -> int:
    """CLI entry point for the ``clean-names`` subcommand."""
    manifests = list(getattr(args, "manifest", None) or [])
    renamed = clean_and_rename(args.target, manifests, dry_run=args.dry_run)
    if args.dry_run:
        print(f"  · Simulación: {renamed} archivo(s) se renombrarían")
    elif renamed:
        print(f"  ✔ Nombres de archivo limpiados: {renamed}")
    return 0
