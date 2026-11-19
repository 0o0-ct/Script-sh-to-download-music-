#!/usr/bin/env python3
"""Download engine for OtterRip using yt-dlp Python API directly.

Replaces multiple yt-dlp subprocesses with a single Python process
that uses the yt-dlp API with internal workers, progress_hooks, and
postprocessor_hooks for real-time progress reporting.

Features:
- Individual song progress and global playlist progress
- Download speed and ETA
- Individual song cancellation
- Automatic retries for recoverable errors
- Structured error logging
- JSON lines output for bash integration

Usage:
    py_core download engine <url> --dest <dir> [options]
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger("otterrip_core.download_engine")

# Try to import yt-dlp
try:
    import yt_dlp

    HAS_YT_DLP = True
except ImportError:
    HAS_YT_DLP = False


# ─── Data classes ───────────────────────────────────────────────────────────


@dataclass
class DownloadEntry:
    """Represents a single download entry from a playlist."""

    url: str
    index: int
    title: str = ""
    artist: str = ""
    album: str = ""
    duration: Optional[int] = None
    webpage_url: str = ""
    id: str = ""


@dataclass
class DownloadResult:
    """Result of a single download attempt."""

    entry: DownloadEntry
    status: str  # "ok", "error", "skipped", "cancelled"
    filename: str = ""
    error: str = ""
    retryable: bool = False
    attempts: int = 1


@dataclass
class DownloadSummary:
    """Summary of a download session."""

    total: int = 0
    ok: int = 0
    errors: int = 0
    skipped: int = 0
    cancelled: int = 0
    duration: float = 0.0
    results: List[DownloadResult] = field(default_factory=list)


# ─── Error classification ───────────────────────────────────────────────────

RECOVERABLE_PATTERNS = [
    r"temporary failure",
    r"network is unreachable",
    r"connection refused",
    r"connection timed out",
    r"too many requests",
    r"429",
    r"503",
    r"500",
    r"rate limit",
    r"retry",
    r"socket error",
    r"eof when reading",
    r"broken pipe",
]

PERMANENT_PATTERNS = [
    r"404",
    r"video unavailable",
    r"private video",
    r"deleted",
    r"not found",
    r"access denied",
    r"403",
    r"sign in to confirm",
    r"age-restricted",
]


def classify_error(error_msg: str) -> tuple[str, bool]:
    """Classify an error as recoverable or permanent.

    Returns:
        (category, retryable) tuple.
    """
    error_lower = error_msg.lower()
    for pattern in RECOVERABLE_PATTERNS:
        if re.search(pattern, error_lower):
            return ("recoverable", True)
    for pattern in PERMANENT_PATTERNS:
        if re.search(pattern, error_lower):
            return ("permanent", False)
    # Default: treat unknown errors as potentially recoverable
    return ("unknown", True)


# ─── Progress reporting ─────────────────────────────────────────────────────


def _format_speed(bytes_per_sec: float) -> str:
    """Format bytes/second to human-readable speed."""
    if bytes_per_sec < 1024:
        return f"{bytes_per_sec:.0f}B/s"
    elif bytes_per_sec < 1024 * 1024:
        return f"{bytes_per_sec / 1024:.1f}KB/s"
    else:
        return f"{bytes_per_sec / (1024 * 1024):.1f}MB/s"


def _format_eta(seconds: float) -> str:
    """Format ETA in seconds to human-readable string."""
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes}m{secs}s"
    else:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        return f"{hours}h{minutes}m"


def _emit_json(event: dict) -> None:
    """Emit a JSON line to stdout for bash integration."""
    line = json.dumps(event, ensure_ascii=False)
    print(line, flush=True)


# ─── Download Engine ────────────────────────────────────────────────────────


class DownloadEngine:
    """Main download engine using yt-dlp Python API.

    Uses internal thread pool for parallel downloads with progress_hooks
    and postprocessor_hooks for real-time reporting.
    """

    def __init__(
        self,
        dest: str,
        format_profile: str = "mp3",
        workers: int = 3,
        archive_path: Optional[str] = None,
        quality: str = "320K",
        dur_min: int = 60,
        dur_max: int = 480,
        fetch_lyrics: bool = True,
        proxy: Optional[str] = None,
        cookies_from_browser: Optional[str] = None,
        cookies_file: Optional[str] = None,
        embed_thumbnail: bool = True,
    ):
        """Initialize the download engine.

        Args:
            dest: Destination directory.
            format_profile: Audio format (original, mp3, m4a, flac, opus, wav).
            workers: Number of parallel workers (1-32).
            archive_path: Path to download archive file.
            quality: Audio quality/bitrate.
            dur_min: Minimum duration in seconds.
            dur_max: Maximum duration in seconds.
            fetch_lyrics: Whether to fetch lyrics.
            proxy: Proxy URL.
            cookies_from_browser: Browser name for cookies.
            cookies_file: Cookies file path.
            embed_thumbnail: Whether to embed video thumbnail.
        """
        self.dest = Path(dest)
        self.format_profile = format_profile.lower()
        self.workers = max(1, min(workers, 32))
        self.archive_path = archive_path
        self.quality = quality
        self.dur_min = dur_min
        self.dur_max = dur_max
        self.fetch_lyrics = fetch_lyrics
        self.proxy = proxy
        self.cookies_from_browser = cookies_from_browser
        self.cookies_file = cookies_file
        self.embed_thumbnail = embed_thumbnail

        # Thread-safe cancellation set
        self._cancelled: Set[str] = set()
        self._cancel_lock = threading.Lock()

        # Results collection
        self._results: List[DownloadResult] = []
        self._results_lock = threading.Lock()

        # Progress tracking
        self._total_entries: int = 0
        self._completed: int = 0
        self._completed_lock = threading.Lock()

        # Global progress
        self._global_downloaded: int = 0
        self._global_total: int = 0
        self._global_lock = threading.Lock()

    def _build_ytdlp_opts(self) -> dict:
        """Build yt-dlp options dict."""
        opts: Dict[str, Any] = {
            "outtmpl": str(self.dest / "%(artist)s - %(title)s.%(ext)s"),
            "quiet": True,
            "no_warnings": False,
            "progress_hooks": [self._progress_hook],
            "postprocessor_hooks": [self._postprocessor_hook],
            "match_filter": self._match_filter_duration,
            "extract_flat": False,
        }

        # Audio format settings
        if self.format_profile == "original":
            # Original: download best audio without conversion
            opts["format"] = "bestaudio/best"
            opts["postprocessors"] = []
        else:
            # Direct-mode conversion: ``FFmpegExtractAudio`` is the
            # mechanism that applies in ``YoutubeDL(...)`` — the
            # ``x``/``audioformat``/``audioquality`` CLI options are
            # inert in this mode (verified against yt_dlp 2026.10.26).
            target = {
                "mp3": "mp3",
                "m4a": "m4a",
                "opus": "opus",
                "flac": "flac",
                "wav": "wav",
            }.get(self.format_profile, "mp3")
            pp_entry: Dict[str, Any] = {
                "key": "FFmpegExtractAudio",
                "preferredcodec": target,
            }
            if self.format_profile in ("mp3", "m4a", "opus") and self.quality:
                # Bitrate target in k (e.g. "320K" -> 320).
                pp_entry["preferredquality"] = int(
                    "".join(ch for ch in str(self.quality) if ch.isdigit())
                )

            pp: List[Dict[str, Any]] = [pp_entry]
            if self.embed_thumbnail:
                pp.append({"key": "FFmpegEmbedThumbnail"})
            opts["postprocessors"] = pp

        # Archive
        if self.archive_path:
            opts["download_archive"] = self.archive_path

        # Lyrics
        if self.fetch_lyrics:
            opts["writelyrics"] = True
            opts["embedlyrics"] = True

        # Metadata
        opts["embedmetadata"] = True
        opts["metadatas"] = [
            "%(title)s:%(meta_title)s",
            "%(artist,uploader,creator)s:%(meta_artist)s",
            "%(album)s:%(meta_album)s",
            "%(genre)s:%(meta_genre)s",
            "%(track_number,playlist_index)s:%(meta_track)s",
        ]

        # Proxy
        if self.proxy:
            opts["proxy"] = self.proxy

        # Cookies
        if self.cookies_from_browser:
            # yt_dlp expects a tuple (browser, [profile, keyring, container]);
            # a string makes _parse_browser_specification raise TypeError.
            opts["cookiesfrombrowser"] = (self.cookies_from_browser,)
        if self.cookies_file:
            opts["cookiefile"] = self.cookies_file

        # Rate limiting
        opts["sleep_requests"] = 1
        opts["sleep_interval"] = 1
        opts["max_sleep_interval"] = 3

        return opts

    def extract_playlist(self, url: str) -> List[DownloadEntry]:
        """Extract songs from a playlist URL without downloading.

        Args:
            url: Playlist or album URL.

        Returns:
            List of DownloadEntry objects.
        """
        if not HAS_YT_DLP:
            _emit_json({"type": "error", "error": "yt-dlp no instalado"})
            return []

        opts = self._build_ytdlp_opts()
        opts["extract_flat"] = "in_playlist"
        opts["playlistend"] = None
        opts["progress_hooks"] = []  # No progress needed for extraction
        opts["postprocessor_hooks"] = []

        entries: List[DownloadEntry] = []

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)

                if info is None:
                    return []

                # Single video
                if "entries" not in info or info["entries"] is None:
                    entry = DownloadEntry(
                        url=info.get("webpage_url", url),
                        index=1,
                        title=info.get("title", ""),
                        artist=info.get("artist", info.get("uploader", "")),
                        album=info.get("album", ""),
                        duration=info.get("duration"),
                        webpage_url=info.get("webpage_url", url),
                        id=info.get("id", ""),
                    )
                    entries.append(entry)
                else:
                    # Playlist
                    for i, entry_info in enumerate(info["entries"], 1):
                        if entry_info is None:
                            continue
                        entry = DownloadEntry(
                            url=entry_info.get("url", entry_info.get("webpage_url", "")),
                            index=i,
                            title=entry_info.get("title", ""),
                            artist=entry_info.get("artist", entry_info.get("uploader", "")),
                            album=entry_info.get("album", info.get("title", "")),
                            duration=entry_info.get("duration"),
                            webpage_url=entry_info.get("webpage_url", ""),
                            id=entry_info.get("id", ""),
                        )
                        entries.append(entry)

        except Exception as exc:
            _emit_json(
                {
                    "type": "error",
                    "error": f"Error extrayendo playlist: {exc}",
                    "retryable": True,
                }
            )

        self._total_entries = len(entries)
        _emit_json(
            {
                "type": "playlist_extracted",
                "total": len(entries),
                "title": info.get("title", "") if "info" in dir() else "",
            }
        )

        return entries

    def _progress_hook(self, d: dict, entry: Optional[DownloadEntry] = None) -> None:
        """Callback for yt-dlp progress_hooks.

        Emits JSON progress events to stdout.
        """
        status = d.get("status", "")
        downloaded = d.get("downloaded_bytes", 0)
        total = d.get("total_bytes", 0) or d.get("total_bytes_estimate", 0)
        speed = d.get("speed", 0) or 0
        eta = d.get("eta", 0) or 0

        title = entry.title if entry else d.get("filename", "unknown")
        index = entry.index if entry else 0
        filename = d.get("filename", "")

        if status == "downloading":
            percent = (downloaded / total * 100) if total > 0 else 0
            _emit_json(
                {
                    "type": "progress",
                    "index": index,
                    "title": title,
                    "percent": round(percent, 1),
                    "speed": _format_speed(speed),
                    "speed_bytes": speed,
                    "eta": _format_eta(eta),
                    "eta_seconds": eta,
                    "filename": os.path.basename(filename) if filename else "",
                    "downloaded_bytes": downloaded,
                    "total_bytes": total,
                }
            )

            # Update global progress
            with self._global_lock:
                self._global_downloaded = downloaded

        elif status == "finished":
            size = d.get("total_bytes", downloaded)
            with self._completed_lock:
                self._completed += 1
            with self._global_lock:
                self._global_total = size

            # Emit global progress
            if self._total_entries > 0:
                global_percent = (self._completed / self._total_entries) * 100
                _emit_json(
                    {
                        "type": "global_progress",
                        "completed": self._completed,
                        "total": self._total_entries,
                        "percent": round(global_percent, 1),
                    }
                )

    def _postprocessor_hook(self, d: dict) -> None:
        """Callback for yt-dlp postprocessor_hooks.

        Collects structured results without parsing logs.
        """
        key = d.get("key", "")
        filename = d.get("filename", "")

        if key == "FFmpegExtractAudio":
            status = d.get("status", "finished")
            _emit_json(
                {
                    "type": "postprocessor",
                    "key": key,
                    "status": status,
                    "filename": os.path.basename(filename) if filename else "",
                }
            )

    def _worker_download(self, entry: DownloadEntry, worker_id: int) -> DownloadResult:
        """Download a single entry using yt-dlp API.

        Args:
            entry: Download entry to process.
            worker_id: Worker identifier for logging.

        Returns:
            DownloadResult with status and metadata.
        """
        # Check cancellation
        with self._cancel_lock:
            if entry.id in self._cancelled or entry.url in self._cancelled:
                return DownloadResult(
                    entry=entry,
                    status="cancelled",
                )

        if not HAS_YT_DLP:
            return DownloadResult(
                entry=entry,
                status="error",
                error="yt-dlp no instalado",
                retryable=False,
            )

        max_attempts = 2  # Initial + 1 retry
        last_error = ""

        for attempt in range(1, max_attempts + 1):
            # Check cancellation before each attempt
            with self._cancel_lock:
                if entry.id in self._cancelled or entry.url in self._cancelled:
                    return DownloadResult(
                        entry=entry,
                        status="cancelled",
                        attempts=attempt,
                    )

            opts = self._build_ytdlp_opts()
            # Use simpler progress hook for workers
            opts["progress_hooks"] = [lambda d, e=entry: self._progress_hook(d, e)]

            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(entry.url, download=True)

                    if info is None:
                        last_error = "No se pudo extraer información"
                        continue

                    filename = info.get("filename", "")
                    result = DownloadResult(
                        entry=entry,
                        status="ok",
                        filename=filename,
                        attempts=attempt,
                    )

                    # Emit finished event
                    _emit_json(
                        {
                            "type": "finished",
                            "index": entry.index,
                            "title": entry.title,
                            "status": "ok",
                            "filename": os.path.basename(filename) if filename else "",
                        }
                    )

                    return result

            except Exception as exc:
                error_msg = str(exc)
                last_error = error_msg
                category, retryable = classify_error(error_msg)

                _emit_json(
                    {
                        "type": "error",
                        "index": entry.index,
                        "title": entry.title,
                        "error": error_msg[:200],
                        "category": category,
                        "retryable": retryable,
                        "attempt": attempt,
                    }
                )

                if not retryable:
                    break  # Don't retry permanent errors
                if attempt >= max_attempts:
                    break

                # Wait before retry
                time.sleep(2 * attempt)

        # All attempts failed
        category, retryable = classify_error(last_error)
        result = DownloadResult(
            entry=entry,
            status="error",
            error=last_error[:500],
            retryable=retryable,
            attempts=max_attempts,
        )

        _emit_json(
            {
                "type": "finished",
                "index": entry.index,
                "title": entry.title,
                "status": "error",
                "error": last_error[:200],
            }
        )

        return result

    def _match_filter_duration(self, info_dict: dict) -> Optional[str]:
        """yt_dlp ``match_filter`` callable: skip entries outside the duration band.

        Returns a skip reason string (not None) when the entry should be
        skipped, or None to accept it.
        """
        duration = info_dict.get("duration")
        if duration is None:
            return None
        if duration < self.dur_min or duration > self.dur_max:
            return f"duration outside [{self.dur_min}, {self.dur_max}]s"
        return None

    def cancel(self, entry_ids: List[str]) -> None:
        """Cancel specific downloads by entry ID or URL.

        Args:
            entry_ids: List of entry IDs or URLs to cancel.
        """
        with self._cancel_lock:
            self._cancelled.update(entry_ids)

    def cancel_all(self) -> None:
        """Cancel all pending downloads."""
        with self._cancel_lock:
            self._cancelled.add("*")

    def download(self, url: str, entries: Optional[List[DownloadEntry]] = None) -> DownloadSummary:
        """Download a URL (single song or playlist).

        Args:
            url: URL to download.
            entries: Pre-extracted entries (optional). If None, will extract.

        Returns:
            DownloadSummary with results.
        """
        start_time = time.time()

        # Create destination
        self.dest.mkdir(parents=True, exist_ok=True)

        # Extract playlist if needed
        if entries is None:
            entries = self.extract_playlist(url)

        if not entries:
            summary = DownloadSummary(total=0, duration=time.time() - start_time)
            _emit_json(
                {
                    "type": "summary",
                    "total": 0,
                    "ok": 0,
                    "errors": 0,
                    "skipped": 0,
                    "cancelled": 0,
                    "duration": round(summary.duration, 1),
                }
            )
            return summary

        self._total_entries = len(entries)
        _emit_json(
            {
                "type": "download_start",
                "total": len(entries),
                "workers": self.workers,
                "dest": str(self.dest),
            }
        )

        # Download using thread pool
        results: List[DownloadResult] = []

        if self.workers == 1:
            # Sequential download
            for entry in entries:
                result = self._worker_download(entry, worker_id=1)
                with self._results_lock:
                    self._results.append(result)
                results.append(result)
        else:
            # Parallel download with thread pool
            with ThreadPoolExecutor(max_workers=self.workers) as executor:
                futures: Dict[Future, DownloadEntry] = {}
                for entry in entries:
                    future = executor.submit(self._worker_download, entry, len(futures) + 1)
                    futures[future] = entry

                for future in as_completed(futures):
                    try:
                        result = future.result()
                        with self._results_lock:
                            self._results.append(result)
                        results.append(result)
                    except Exception as exc:
                        entry = futures[future]
                        error_result = DownloadResult(
                            entry=entry,
                            status="error",
                            error=str(exc)[:500],
                            retryable=True,
                        )
                        with self._results_lock:
                            self._results.append(error_result)
                        results.append(error_result)

        # Build summary
        summary = DownloadSummary(
            total=len(entries),
            ok=sum(1 for r in results if r.status == "ok"),
            errors=sum(1 for r in results if r.status == "error"),
            skipped=sum(1 for r in results if r.status == "skipped"),
            cancelled=sum(1 for r in results if r.status == "cancelled"),
            duration=time.time() - start_time,
            results=results,
        )

        # Emit summary
        _emit_json(
            {
                "type": "summary",
                "total": summary.total,
                "ok": summary.ok,
                "errors": summary.errors,
                "skipped": summary.skipped,
                "cancelled": summary.cancelled,
                "duration": round(summary.duration, 1),
            }
        )

        return summary


# ─── CLI entry point ────────────────────────────────────────────────────────


def run_download_engine(args) -> int:
    """CLI entry point for download engine.

    Args:
        args: Parsed arguments with url, dest, format, workers, etc.

    Returns:
        Exit code (0 for success, 1 for error).
    """
    if not HAS_YT_DLP:
        print("Error: yt-dlp no está instalado. Instala con: pip install yt-dlp", file=sys.stderr)
        return 1

    engine = DownloadEngine(
        dest=args.dest,
        format_profile=getattr(args, "format", "mp3") or "mp3",
        workers=getattr(args, "workers", 3) or 3,
        archive_path=getattr(args, "archive", None),
        quality=getattr(args, "quality", "320K") or "320K",
        dur_min=getattr(args, "dur_min", 60) or 60,
        dur_max=getattr(args, "dur_max", 480) or 480,
        fetch_lyrics=not getattr(args, "no_lyrics", False),
        proxy=getattr(args, "proxy", None),
        cookies_from_browser=getattr(args, "cookies_from_browser", None),
        cookies_file=getattr(args, "cookies", None),
    )

    try:
        summary = engine.download(args.url)
    except KeyboardInterrupt:
        print("\nDescarga cancelada por el usuario.", file=sys.stderr)
        engine.cancel_all()
        return 130
    except Exception as exc:
        _emit_json(
            {
                "type": "error",
                "error": f"Error en el motor de descargas: {exc}",
            }
        )
        return 1

    if summary.errors > 0 and summary.ok == 0:
        return 1
    return 0


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="OtterRip Download Engine")
    parser.add_argument("url", help="URL to download")
    parser.add_argument("--dest", required=True, help="Destination directory")
    parser.add_argument("--format", default="mp3", help="Audio format")
    parser.add_argument("--workers", type=int, default=3, help="Parallel workers")
    parser.add_argument("--archive", help="Download archive file")
    parser.add_argument("--quality", default="320K", help="Audio quality")
    parser.add_argument("--dur-min", type=int, default=60, help="Min duration")
    parser.add_argument("--dur-max", type=int, default=480, help="Max duration")
    parser.add_argument("--no-lyrics", action="store_true", help="Skip lyrics")
    parser.add_argument("--proxy", help="Proxy URL")
    parser.add_argument("--cookies-from-browser", help="Browser cookies")
    parser.add_argument("--cookies", help="Cookies file")

    args = parser.parse_args()
    sys.exit(run_download_engine(args))
