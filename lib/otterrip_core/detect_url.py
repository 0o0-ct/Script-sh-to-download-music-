#!/usr/bin/env python3
"""URL detection module for OtterRip.

Classifies YouTube / YouTube Music URLs as song, playlist, or album
by querying yt-dlp for structured metadata.  Falls back to URL-pattern
heuristics when the network call fails or times out.

This is the single source of truth for URL classification.  The Bash
launcher delegates to this module instead of duplicating the logic.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from typing import Any, Dict, Optional


# ── Data types ─────────────────────────────────────────────────────

class DetectionResult:
    """Structured result of a URL classification."""

    __slots__ = ("kind", "count", "title", "source")

    def __init__(
        self,
        kind: str,
        count: Optional[int] = None,
        title: str = "",
        source: str = "unknown",
    ) -> None:
        self.kind = kind       # "song" | "playlist" | "album"
        self.count = count     # number of entries (None for songs / unknown)
        self.title = title
        self.source = source   # "yt-dlp" | "fallback" | "error"

    def to_output(self) -> str:
        """Return the 4-line format expected by the Bash launcher."""
        count_str = str(self.count) if self.count is not None else ""
        title_clean = re.sub(r"[\x00-\x1f\x7f]+", " ", self.title or "")
        title_clean = re.sub(r"\s+", " ", title_clean).strip()
        return f"{self.kind}\n{count_str}\n{title_clean}\n{self.source}"


# ── URL pattern heuristics (fallback only) ─────────────────────────

def _url_hint(url: str, no_playlist: bool = False) -> str:
    """Very coarse hint from URL structure — used ONLY as fallback."""
    u = url.lower()
    album = any(seg in u for seg in ("/album/", "/albums/", "olak5uy_"))
    collection = any(
        seg in u for seg in ("/playlist", "/sets/", "/album/", "/albums/", "list=")
    )
    if no_playlist and "/watch" in u:
        collection = False
    if album:
        return "album"
    if collection:
        return "playlist"
    return "song"


# ── yt-dlp metadata query ─────────────────────────────────────────

_FIELDS = [
    "playlist_count",
    "playlist_title",
    "album",
    "title",
    "webpage_url_domain",
    "playlist_id",
    "webpage_url",
    "_type",
]

_TEMPLATE = "\x1f".join(f"%({name})j" for name in _FIELDS)


def _build_args(
    url: str,
    ytdlp_bin: str = "",
    js_args: list[str] | None = None,
    cookies_file: str = "",
    cookies_browser: str = "",
    no_playlist: bool = False,
    flat: bool = True,
) -> list[str]:
    """Build the argument list for a single yt-dlp invocation."""
    if ytdlp_bin:
        cmd: list[str] = [ytdlp_bin]
    else:
        cmd = [sys.executable, "-m", "yt_dlp"]

    args: list[str] = list(cmd)
    args.extend([
        "--playlist-items", "1",
        "--skip-download",
        "--no-warnings",
        "--ignore-errors",
        "--socket-timeout", "20",
        "--print", _TEMPLATE,
    ])

    if flat:
        args.insert(len(cmd), "--flat-playlist")
    if no_playlist:
        args.append("--no-playlist")

    if js_args:
        args.extend(js_args)

    if cookies_file and os.path.isfile(cookies_file):
        args.extend(["--cookies", cookies_file])
    elif cookies_browser:
        args.extend(["--cookies-from-browser", cookies_browser])

    # "--" prevents the URL from being interpreted as an option
    args.append("--")
    args.append(url)
    return args


def _decode_field(value: str) -> str:
    """Decode a JSON-encoded field from yt-dlp %()j output."""
    value = (value or "").strip()
    if not value or value == "NA":
        return ""
    try:
        obj: Any = json.loads(value)
    except (json.JSONDecodeError, ValueError):
        return value
    if obj is None:
        return ""
    return str(obj)


def _query_ytdlp(
    url: str,
    ytdlp_bin: str = "",
    js_args: list[str] | None = None,
    cookies_file: str = "",
    cookies_browser: str = "",
    no_playlist: bool = False,
    timeout: int = 50,
) -> Optional[Dict[str, str]]:
    """Run yt-dlp and return a dict of field values, or None on failure."""
    for flat in (True, False):
        args = _build_args(
            url, ytdlp_bin, js_args, cookies_file, cookies_browser,
            no_playlist, flat,
        )
        try:
            proc = subprocess.run(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
        except (TimeoutError, OSError):
            continue

        stdout = proc.stdout or ""
        for line in stdout.splitlines():
            if line.count("\x1f") >= len(_FIELDS) - 1:
                parts = line.split("\x1f")
                while len(parts) < len(_FIELDS):
                    parts.append("")
                return dict(zip(_FIELDS, (_decode_field(p) for p in parts)))
    return None


# ── Classification logic ───────────────────────────────────────────

def classify_url(
    url: str,
    ytdlp_bin: str = "",
    js_args: list[str] | None = None,
    cookies_file: str = "",
    cookies_browser: str = "",
    no_playlist: bool = False,
    timeout: int = 50,
) -> DetectionResult:
    """Classify *url* as song, playlist, or album.

    Parameters
    ----------
    url:
        The YouTube / YouTube Music URL to classify.
    ytdlp_bin:
        Absolute path to the yt-dlp binary.  Empty string uses
        ``python -m yt_dlp``.
    js_args:
        Extra arguments for JS runtime (e.g. ``["--js-runtimes", "node"]``).
    cookies_file / cookies_browser:
        Cookie configuration passed through to yt-dlp.
    no_playlist:
        If True, a ``/watch`` URL with ``list=`` is treated as a single
        song, not as a playlist download.
    timeout:
        Maximum seconds for a single yt-dlp invocation.

    Returns
    -------
    DetectionResult
        A named tuple with *kind*, *count*, *title*, and *source*.
    """
    if not url:
        return DetectionResult("song", count=1, source="error")

    # ── Fast path: query yt-dlp ──────────────────────────────────
    fields = _query_ytdlp(
        url, ytdlp_bin, js_args, cookies_file, cookies_browser,
        no_playlist, timeout,
    )

    if fields is None:
        # Network failure, timeout, or yt-dlp not available
        kind = _url_hint(url, no_playlist)
        return DetectionResult(kind, source="fallback")

    raw_count = fields.get("playlist_count", "")
    count = int(raw_count) if raw_count.isdigit() else 0
    ptitle = fields.get("playlist_title", "")
    album = fields.get("album", "")
    title = fields.get("title", "")
    domain = fields.get("webpage_url_domain", "")
    pid = fields.get("playlist_id", "")
    page = fields.get("webpage_url", "")
    _type = fields.get("_type", "")

    url_l = url.lower()
    page_l = (page or "").lower()

    # Is this a pure collection URL (playlist/album page, not /watch)?
    pure_collection = any(
        seg in url_l for seg in ("/playlist", "/sets/", "/album/", "/albums/")
    )

    # Collection if yt-dlp reports playlist metadata or >1 entries
    collection = bool(ptitle or pid) or count > 1 or pure_collection
    if no_playlist and not pure_collection:
        collection = False

    # Album detection: music.youtube.com + OLAK5uy_ playlist ID,
    # or /album/ path, or album title == playlist title
    album_flag = (
        "/album/" in page_l
        or "/albums/" in page_l
        or "/album/" in url_l
        or "/albums/" in url_l
        or ("music.youtube.com" in domain.lower() and pid.startswith("OLAK5uy_"))
        or (album and ptitle and album.casefold().strip() == ptitle.casefold().strip())
    )

    if collection and album_flag:
        kind = "album"
    elif collection:
        kind = "playlist"
    else:
        kind = "song"

    if collection:
        out_title = ptitle or album or title
        out_count = count if count > 0 else None
    else:
        out_title = title
        out_count = 1

    return DetectionResult(kind, count=out_count, title=out_title, source="yt-dlp")


# ── CLI entry-point ────────────────────────────────────────────────

def run_detect_url(args) -> int:
    """Handler for ``python -m otterrip_core detect-url``."""
    url = getattr(args, "url", "")
    ytdlp_bin = getattr(args, "ytdlp", "") or ""
    js_args_raw = getattr(args, "js_args", "") or ""
    cookies_file = getattr(args, "cookies_file", "") or ""
    cookies_browser = getattr(args, "cookies_browser", "") or ""
    no_playlist = getattr(args, "no_playlist", False)
    timeout = getattr(args, "timeout", 50) or 50

    js_args = [a for a in js_args_raw.split() if a] if js_args_raw else []

    result = classify_url(
        url=url,
        ytdlp_bin=ytdlp_bin,
        js_args=js_args or None,
        cookies_file=cookies_file,
        cookies_browser=cookies_browser,
        no_playlist=no_playlist,
        timeout=timeout,
    )
    print(result.to_output())
    return 0


def main() -> int:
    """CLI entry-point for ``python -m otterrip_core detect-url``."""
    parser = argparse.ArgumentParser(
        prog="detect-url",
        description="Classify a YouTube URL as song, playlist, or album.",
    )
    parser.add_argument("--url", required=True, help="URL to classify")
    parser.add_argument("--ytdlp", default="", help="Path to yt-dlp binary")
    parser.add_argument(
        "--js-args", default="", help="Space-separated JS runtime args"
    )
    parser.add_argument("--cookies-file", default="", help="Netrc/cookies file")
    parser.add_argument(
        "--cookies-browser", default="", help="Browser name for cookies"
    )
    parser.add_argument(
        "--no-playlist", action="store_true",
        help="Treat /watch?...&list=... as a single song",
    )
    parser.add_argument(
        "--timeout", type=int, default=50, help="Timeout in seconds for yt-dlp"
    )

    args = parser.parse_args()
    js_args = [a for a in args.js_args.split() if a] if args.js_args else []

    result = classify_url(
        url=args.url,
        ytdlp_bin=args.ytdlp,
        js_args=js_args or None,
        cookies_file=args.cookies_file,
        cookies_browser=args.cookies_browser,
        no_playlist=args.no_playlist,
        timeout=args.timeout,
    )
    print(result.to_output())
    return 0


if __name__ == "__main__":
    sys.exit(main())
