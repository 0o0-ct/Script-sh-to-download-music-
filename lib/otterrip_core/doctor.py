#!/usr/bin/env python3
"""System diagnostics (doctor command)."""
from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path


def _check(binary: str, min_version: str | None = None) -> tuple[bool, str]:
    path = shutil.which(binary)
    if not path:
        return False, f"  ! {binary} no encontrado"
    ver = ""
    try:
        out = subprocess.check_output(
            [path, "--version"], stderr=subprocess.STDOUT, timeout=10
        ).decode(errors="replace")
        ver = out.strip().split("\n")[0]
    except Exception:
        ver = "(desconocido)"
    return True, f"  ✔ {binary} {ver}"


def run_doctor(args) -> int:
    """Run system diagnostics and print results."""
    dest = getattr(args, "dest", "") or ""

    print()
    print("  ── OtterRip Doctor ──")
    print()

    # Python
    py_ver = sys.version.split()[0]
    print(f"  ✔ Python {py_ver} ({sys.executable})")

    # yt-dlp
    ok, msg = _check("yt-dlp")
    print(msg)
    if not ok:
        print("      → Instala: pip install yt-dlp[default]")

    # ffmpeg
    ok, msg = _check("ffmpeg")
    print(msg)
    if not ok:
        print("      → Instala: sudo apt install ffmpeg  (o tu gestor de paquetes)")

    # ffprobe
    ok, msg = _check("ffprobe")
    print(msg)

    # Mutagen
    has_mutagen = importlib.util.find_spec("mutagen") is not None
    if has_mutagen:
        try:
            import mutagen
            print(f"  ✔ mutagen {mutagen.version_string}")
        except Exception:
            print("  ✔ mutagen (instalado)")
    else:
        print("  ! mutagen no encontrado")
        print("      → Instala: pip install mutagen")

    # requests
    has_requests = importlib.util.find_spec("requests") is not None
    if has_requests:
        try:
            import requests
            print(f"  ✔ requests {requests.__version__}")
        except Exception:
            print("  ✔ requests (instalado)")
    else:
        print("  ! requests no encontrado")
        print("      → Instala: pip install requests")

    # aiohttp
    has_aiohttp = importlib.util.find_spec("aiohttp") is not None
    if has_aiohttp:
        try:
            import aiohttp
            print(f"  ✔ aiohttp {aiohttp.__version__}")
        except Exception:
            print("  ✔ aiohttp (instalado)")
    else:
        print("  ! aiohttp no encontrado")
        print("      → Instala: pip install aiohttp")

    # syncedlyrics (optional)
    has_synced = importlib.util.find_spec("syncedlyrics") is not None
    if has_synced:
        print("  ✔ syncedlyrics (Multi-Web disponible)")
    else:
        print("  ℹ syncedlyrics no instalado (Multi-Web desactivado)")
        print("      → Opcional: pip install syncedlyrics")

    # Destination directory
    if dest:
        d = Path(dest)
        if d.exists():
            if d.is_dir() and d.is_writable() if hasattr(d, 'is_writable') else d.is_dir():
                print(f"  ✔ Destino: {dest}")
            else:
                print(f"  ! Destino no escribible: {dest}")
        else:
            print(f"  ℹ Destino no existe (se creará): {dest}")

    # Network check
    try:
        import requests as req
        req.get("https://lrclib.net", timeout=5)
        print("  ✔ Red: conexión a lrclib.net OK")
    except Exception:
        print("  ! Red: no se pudo conectar a lrclib.net")

    print()
    return 0
