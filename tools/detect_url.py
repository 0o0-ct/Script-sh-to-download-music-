#!/usr/bin/env python3
"""Detección del tipo de URL para OtterRip (canción, playlist o álbum).

Se invoca desde `Down-Linux-music_v7.sh`:

    python3 lib/detect_url.py --url "<URL>" [--no-playlist] ...

Salida (stdout, una línea por campo):

    1. tipo:      song | playlist | album
    2. cantidad:  número de elementos (o vacío)
    3. título:    título de la colección o de la pista
    4. estado:    ok | fallback

Si no se puede consultar yt-dlp se devuelve "fallback" y el script aplica la
detección heurística based en la URL.

Este módulo NO se ejecuta directamente desde el paquete del backend: es una
herramienta del script de arranque y por eso no figura en REQUIRED_FILES.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

# Límite (segundos) del subprocess de yt-dlp.
DEFAULT_TIMEOUT = 50


def url_hint(value: str, no_playlist: bool) -> str:
    """Clasifica la URL por su forma (último recurso)."""
    u = value.lower()
    album = "/album/" in u or "/albums/" in u or "olak5uy_" in u
    collection = album or "/playlist" in u or "/sets/" in u or "list=" in u
    if no_playlist and "/watch" in u:
        collection = False
    return "album" if album else ("playlist" if collection else "song")


def clean(value: str) -> str:
    """Quita caracteres de control y colapsa espacios."""
    value = re.sub(r"[\x00-\x1f\x7f]+", " ", value or "")
    return re.sub(r"\s+", " ", value).strip()


def decode_field(value: str) -> str:
    """yt-dlp puede devolver JSON en algunos campos; se normaliza a texto."""
    value = (value or "").strip()
    if not value or value == "NA":
        return ""
    try:
        obj = json.loads(value)
    except Exception:
        return value
    if obj is None:
        return ""
    return str(obj)


def print_fallback(url: str, no_playlist: bool) -> None:
    """Imprime la estimación mínima cuando yt-dlp no responde."""
    kind = url_hint(url, no_playlist)
    print(kind)
    print("1" if kind == "song" else "")
    print("")
    print("fallback")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="detect_url.py",
        description="Detecta si una URL es canción, playlist o álbum",
    )
    parser.add_argument("--url", required=True, help="URL a analizar")
    parser.add_argument(
        "--no-playlist",
        action="store_true",
        help="Tratar los enlaces /watch?...&list=... como canción",
    )
    parser.add_argument("--ytdlp", default="", help="Ruta del binario de yt-dlp")
    parser.add_argument("--js-args", default="", help="Argumentos de runtime JS")
    parser.add_argument("--cookies-file", default="", help="Archivo de cookies")
    parser.add_argument("--cookies-browser", default="", help="Navegador de cookies")
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help="Límite de tiempo del subprocess (segundos)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    url = args.url
    no_playlist = bool(args.no_playlist)
    ytdlp_bin = args.ytdlp.strip()
    js_args = [a for a in args.js_args.split() if a]
    cookies_file = args.cookies_file.strip()
    cookies_browser = args.cookies_browser.strip()

    fields = [
        "playlist_count", "playlist_title", "album", "title",
        "webpage_url_domain", "playlist_id", "webpage_url", "_type",
    ]
    template = "\x1f".join(f"%({name})j" for name in fields)
    base = [ytdlp_bin] if ytdlp_bin else [sys.executable, "-m", "yt_dlp"]

    def try_run(flat: bool) -> str:
        cmd = base + [
            "--playlist-items", "1",
            "--skip-download", "--no-warnings", "--ignore-errors",
            "--socket-timeout", "20",
            "--print", template,
        ]
        cmd.extend(js_args)
        if cookies_file and os.path.isfile(cookies_file) and os.access(cookies_file, os.R_OK):
            cmd.extend(["--cookies", cookies_file])
        elif cookies_browser:
            cmd.extend(["--cookies-from-browser", cookies_browser])
        if flat:
            cmd.insert(len(base), "--flat-playlist")
        if no_playlist:
            cmd.append("--no-playlist")
        # "--" evita que una URL que empiece por "-" se interprete como opción.
        cmd.append("--")
        cmd.append(url)
        try:
            proc = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace",
                timeout=max(int(args.timeout), 1),
            )
        except Exception:
            return ""
        for line in (proc.stdout or "").splitlines():
            if line.count("\x1f") >= len(fields) - 1:
                return line
        return ""

    line = try_run(True) or try_run(False)
    if not line:
        print_fallback(url, no_playlist)
        return 0

    parts = line.split("\x1f")
    while len(parts) < len(fields):
        parts.append("")
    vals = [decode_field(p) for p in parts[: len(fields)]]

    raw_count = vals[0]
    count = int(raw_count) if raw_count.isdigit() else 0
    ptitle, album, title, domain, pid, page, _typ = vals[1], vals[2], vals[3], vals[4], vals[5], vals[6], vals[7]
    url_l = url.lower()
    page_l = page.lower()

    pure_collection = any(x in url_l for x in ("/playlist", "/sets/", "/album/", "/albums/"))
    collection = bool(ptitle or pid) or count > 1 or pure_collection
    if no_playlist and not pure_collection:
        collection = False

    album_flag = (
        "/album/" in page_l or "/albums/" in page_l
        or "/album/" in url_l or "/albums/" in url_l
        or ("music.youtube.com" in domain.lower() and pid.startswith("OLAK5uy_"))
        or (album and ptitle and album.casefold().strip() == ptitle.casefold().strip())
    )

    kind = "album" if (collection and album_flag) else ("playlist" if collection else "song")
    out_title = (ptitle or album or title) if collection else title
    out_count = (count if count > 0 else "") if collection else 1
    print(kind)
    print(out_count)
    print(clean(out_title))
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
