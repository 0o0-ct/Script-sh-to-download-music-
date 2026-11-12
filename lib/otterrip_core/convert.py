#!/usr/bin/env python3
"""Audio converter (convert command)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".ogg", ".wav"}

EXT_TO_CONTAINER = {
    "mp3": "mp3",
    "flac": "flac",
    "m4a": "ipod",
    "ogg": "ogg",
    "wav": "wav",
}


def _build_ffmpeg_cmd(src: Path, dst: Path, quality: str) -> list[str]:
    ext = dst.suffix.lower()
    cmd = [
        "ffmpeg", "-y", "-i", str(src),
        "-map_metadata", "0",
    ]

    if ext == ".mp3":
        if quality.startswith("k") or quality.isdigit():
            cmd.extend(["-codec:a", f"libmp3lame", "-b:a", quality])
        elif quality == "max":
            cmd.extend(["-codec:a", "libmp3lame", "-b:a", "320k"])
        elif quality == "high":
            cmd.extend(["-codec:a", "libmp3lame", "-b:a", "256k"])
        elif quality == "medium":
            cmd.extend(["-codec:a", "libmp3lame", "-b:a", "192k"])
        elif quality == "low":
            cmd.extend(["-codec:a", "libmp3lame", "-b:a", "128k"])
        else:
            cmd.extend(["-codec:a", "libmp3lame", "-b:a", "256k"])
    elif ext == ".flac":
        if quality == "max" or quality == "24":
            cmd.extend(["-codec:a", "flac", "-sample_fmt", "s24"])
        else:
            cmd.extend(["-codec:a", "flac"])
    elif ext == ".m4a":
        if quality == "max":
            cmd.extend(["-codec:a", "aac", "-b:a", "320k"])
        elif quality == "high":
            cmd.extend(["-codec:a", "aac", "-b:a", "256k"])
        elif quality == "medium":
            cmd.extend(["-codec:a", "aac", "-b:a", "192k"])
        elif quality == "low":
            cmd.extend(["-codec:a", "aac", "-b:a", "128k"])
        else:
            cmd.extend(["-codec:a", "aac", "-b:a", "256k"])
    elif ext == ".ogg":
        q_val = quality.replace("q", "") if quality.startswith("q") else "5"
        cmd.extend(["-codec:a", "libvorbis", "-q:a", q_val])
    elif ext == ".wav":
        if quality == "24":
            cmd.extend(["-sample_fmt", "s24", "-codec:a", "pcm_s24le"])
        else:
            cmd.extend(["-codec:a", "pcm_s16le"])
    else:
        cmd.extend(["-codec:a", "copy"])

    # Preserve cover
    cmd.extend(["-map", "0"])
    cmd.append(str(dst))
    return cmd


def run_convert(args) -> int:
    """Convert audio files."""
    source = Path(args.source)
    to = args.to.lower()
    dest = Path(args.dest)
    quality = args.quality or ""

    if not shutil.which("ffmpeg"):
        print("  ffmpeg no encontrado. Instala ffmpeg para usar el conversor.")
        return 1

    dest.mkdir(parents=True, exist_ok=True)

    if source.is_file():
        files = [source]
    elif source.is_dir():
        files = sorted(
            f for f in source.rglob("*")
            if f.is_file() and f.suffix.lower() in AUDIO_EXTS
        )
    else:
        print(f"Ruta no encontrada: {args.source}")
        return 1

    if not files:
        print("No se encontraron archivos de audio.")
        return 0

    converted = 0
    for fpath in files:
        out_name = fpath.stem + f".{to}"
        out_path = dest / out_name
        if out_path.exists():
            print(f"  Saltando (ya existe): {out_name}")
            continue
        try:
            cmd = _build_ffmpeg_cmd(fpath, out_path, quality)
            subprocess.run(cmd, check=True, capture_output=True, timeout=300)
            print(f"  Convertido: {fpath.name} → {out_name}")
            converted += 1
        except subprocess.CalledProcessError as exc:
            print(f"  Error convirtiendo {fpath.name}: {exc.stderr.decode(errors='replace')[:200]}")
        except subprocess.TimeoutExpired:
            print(f"  Timeout convirtiendo {fpath.name}")

    print(f"  Convertidos: {converted}/{len(files)}")
    return 0
