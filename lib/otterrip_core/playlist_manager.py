#!/usr/bin/env python3
"""Playlist manager for OtterRip.

Export playlists to M3U, sync playlists with local folders,
and detect new/removed songs.

Uses yt-dlp API (flat) to extract playlist info and mutagen
to read tags from local audio files.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

try:
    import mutagen
    from mutagen.flac import FLAC
    from mutagen.mp3 import MP3
    from mutagen.mp4 import MP4

    HAS_MUTAGEN = True
except ImportError:
    HAS_MUTAGEN = False

AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav"}


def _normalize(text: str) -> str:
    """Normalize text for comparison: lowercase, strip, remove punctuation."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def _read_tags(filepath: Path) -> dict:
    """Extract tags from an audio file using mutagen."""
    tags = {"file": str(filepath), "name": filepath.name}
    if not HAS_MUTAGEN:
        return tags
    try:
        ext = filepath.suffix.lower()
        if ext == ".mp3":
            audio = MP3(str(filepath))
        elif ext == ".flac":
            audio = FLAC(str(filepath))
        elif ext == ".m4a":
            audio = MP4(str(filepath))
        else:
            audio = mutagen.File(str(filepath))
        if audio is None:
            return tags
        if hasattr(audio, "info") and audio.info:
            tags["duration"] = round(audio.info.length, 1)
        if isinstance(audio, MP3) and audio.tags:
            tags["title"] = str(audio.tags.get("TIT2", ""))
            tags["artist"] = str(audio.tags.get("TPE1", ""))
            tags["album"] = str(audio.tags.get("TALB", ""))
            tags["track"] = str(audio.tags.get("TRCK", ""))
            tags["has_cover"] = bool(audio.tags.get("APIC:"))
            tags["has_lyrics"] = bool(audio.tags.get("USLT:"))
        elif isinstance(audio, FLAC) and audio.tags:
            tags["title"] = audio.get("title", [""])[0]
            tags["artist"] = audio.get("artist", [""])[0]
            tags["album"] = audio.get("album", [""])[0]
            tags["track"] = audio.get("tracknumber", [""])[0]
            tags["has_cover"] = bool(audio.get("COVERART"))
            tags["has_lyrics"] = bool(audio.get("LYRICS"))
        elif isinstance(audio, MP4) and audio.tags:
            tags["title"] = audio.get("\xa9nam", [""])[0]
            tags["artist"] = audio.get("\xa9ART", [""])[0]
            tags["album"] = audio.get("\xa9alb", [""])[0]
            tags["date"] = audio.get("\xa9day", [""])[0]
            tags["has_cover"] = bool(audio.tags.get("covr"))
            tags["has_lyrics"] = bool(audio.tags.get("\xa9lyr"))
    except Exception:
        pass
    return tags


def _extract_playlist(url: str) -> list[dict]:
    """Extract playlist entries using yt-dlp API (flat mode).

    Returns a list of dicts with 'title', 'artist', 'webpage_url', 'id'.
    """
    entries = []
    try:
        import yt_dlp as ytdl

        ydl_opts = {
            "flat_playlists": True,
            "quiet": True,
            "no_warnings": True,
            "extract_flat": True,
        }
        with ytdl.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info is None:
                return entries

            # Handle playlist
            playlist_entries = info.get("_entries", [])
            if not playlist_entries:
                # Single video
                playlist_entries = [info]

            for entry in playlist_entries:
                if entry is None:
                    continue
                title = entry.get("title", "")
                artist = entry.get("artist", "")
                # Try to get artist from uploader if not set
                if not artist:
                    artist = entry.get("uploader", "")
                if not artist:
                    artist = entry.get("channel", "")
                webpage_url = entry.get("url", entry.get("webpage_url", ""))
                video_id = entry.get("id", "")
                duration = entry.get("duration", 0)

                entries.append(
                    {
                        "title": title,
                        "artist": artist,
                        "url": webpage_url,
                        "id": video_id,
                        "duration": duration or 0,
                    }
                )
    except Exception as exc:
        print(f"  Error extrayendo playlist: {exc}", file=sys.stderr)

    return entries


class PlaylistManager:
    """Manage playlists: export to M3U, sync with local folders."""

    def __init__(self, dest: str = ""):
        self.dest = Path(dest) if dest else Path.cwd()

    def export(self, url: str, dest: str = "") -> str:
        """Export a playlist to an M3U file.

        Args:
            url: YouTube playlist URL.
            dest: Destination directory for the .m3u file.

        Returns:
            Path to the generated .m3u file.
        """
        dest_path = Path(dest) if dest else self.dest
        dest_path.mkdir(parents=True, exist_ok=True)

        entries = _extract_playlist(url)
        if not entries:
            print("  No se encontraron canciones en la playlist.", file=sys.stderr)
            return ""

        # Generate M3U filename from playlist title
        playlist_name = "playlist"
        if entries:
            # Use a sanitized version of the first entry's title
            first_title = entries[0].get("title", "playlist")
            playlist_name = re.sub(r"[^\w\s-]", "", first_title)[:50]
            playlist_name = re.sub(r"\s+", "_", playlist_name).lower()

        m3u_path = dest_path / f"{playlist_name}.m3u"
        lines = ["#EXTM3U"]
        for entry in entries:
            duration = entry.get("duration", 0)
            title = entry.get("title", "")
            artist = entry.get("artist", "")
            display = f"{artist} - {title}" if artist else title
            lines.append(f"#EXTINF:{duration},{display}")
            lines.append(entry.get("url", ""))

        m3u_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return str(m3u_path)

    def scan_local(self, dest: str = "") -> list[dict]:
        """Scan a local directory for audio files and return metadata.

        Args:
            dest: Directory to scan.

        Returns:
            List of dicts with 'file', 'title', 'artist', 'album', etc.
        """
        dest_path = Path(dest) if dest else self.dest
        if not dest_path.is_dir():
            return []

        files = sorted(f for f in dest_path.rglob("*") if f.is_file() and f.suffix.lower() in AUDIO_EXTS)
        return [_read_tags(f) for f in files]

    def sync(self, url: str, dest: str = "") -> dict:
        """Sync a remote playlist with a local directory.

        Compares the remote playlist with local audio files by
        normalizing title+artist. Returns new, removed, and unchanged songs.

        Args:
            url: YouTube playlist URL.
            dest: Local directory to sync with.

        Returns:
            Dict with 'new', 'removed', 'unchanged' lists.
        """
        dest_path = Path(dest) if dest else self.dest
        dest_path.mkdir(parents=True, exist_ok=True)

        # Extract remote playlist
        remote_entries = _extract_playlist(url)
        if not remote_entries:
            print("  No se encontraron canciones en la playlist.", file=sys.stderr)
            return {"new": [], "removed": [], "unchanged": []}

        # Scan local files
        local_files = self.scan_local(str(dest_path))

        # Build lookup by normalized title+artist
        local_lookup: dict[str, list[dict]] = {}
        for info in local_files:
            title = _normalize(info.get("title", ""))
            artist = _normalize(info.get("artist", ""))
            key = f"{artist} - {title}" if artist else title
            if key:
                local_lookup.setdefault(key, []).append(info)

        # Compare
        new_songs = []
        unchanged_songs = []
        matched_local_keys: set[str] = set()

        for entry in remote_entries:
            title = _normalize(entry.get("title", ""))
            artist = _normalize(entry.get("artist", ""))
            key = f"{artist} - {title}" if artist else title

            if key and key in local_lookup:
                # Found a match
                match = local_lookup[key][0]
                unchanged_songs.append(
                    {
                        "remote": entry,
                        "local": match,
                    }
                )
                matched_local_keys.add(key)
            else:
                new_songs.append(entry)

        # Removed: local files not in remote playlist
        removed_songs = []
        for info in local_files:
            title = _normalize(info.get("title", ""))
            artist = _normalize(info.get("artist", ""))
            key = f"{artist} - {title}" if artist else title
            if key and key not in matched_local_keys:
                removed_songs.append(info)

        return {
            "new": new_songs,
            "removed": removed_songs,
            "unchanged": unchanged_songs,
        }

    def list_local(self, dest: str = "") -> list[dict]:
        """List all audio files in a directory with metadata.

        Args:
            dest: Directory to list.

        Returns:
            List of dicts with file info and tags.
        """
        return self.scan_local(dest)


def run_playlist(args) -> int:
    """CLI handler for playlist subcommands."""
    cmd = getattr(args, "playlist_cmd", None)
    if not cmd:
        print("Usage: playlist {export|sync|list}")
        return 1

    manager = PlaylistManager(getattr(args, "dest", "") or "")

    if cmd == "export":
        url = getattr(args, "url", "")
        if not url:
            print("Error: se requiere una URL", file=sys.stderr)
            return 1
        dest = getattr(args, "dest", "") or ""
        m3u_path = manager.export(url, dest)
        if m3u_path:
            print(f"Playlist exportada: {m3u_path}")
            return 0
        return 1

    elif cmd == "sync":
        url = getattr(args, "url", "")
        if not url:
            print("Error: se requiere una URL", file=sys.stderr)
            return 1
        dest = getattr(args, "dest", "") or ""
        result = manager.sync(url, dest)

        new_count = len(result["new"])
        removed_count = len(result["removed"])
        unchanged_count = len(result["unchanged"])

        print()
        print("  ── Sincronización de playlist ──")
        print()
        print(f"  Nuevas canciones:    {new_count}")
        print(f"  Sin cambios:         {unchanged_count}")
        print(f"  Eliminadas local:    {removed_count}")
        print()

        if new_count > 0:
            print("  Canciones nuevas:")
            for song in result["new"][:20]:
                title = song.get("title", "?")
                artist = song.get("artist", "")
                print(f"    • {artist} - {title}" if artist else f"    • {title}")
            if new_count > 20:
                print(f"    ... y {new_count - 20} más")

        if removed_count > 0:
            print()
            print("  Canciones eliminadas (locales):")
            for song in result["removed"][:20]:
                title = song.get("title", "?")
                artist = song.get("artist", "")
                print(f"    • {artist} - {title}" if artist else f"    • {title}")
            if removed_count > 20:
                print(f"    ... y {removed_count - 20} más")

        # Output JSON if requested
        if getattr(args, "json", False):
            print()
            print(json.dumps(result, ensure_ascii=False, default=str))

        return 0

    elif cmd == "list":
        dest = getattr(args, "dest", "") or ""
        files = manager.list_local(dest)
        if not files:
            print("No se encontraron archivos de audio.")
            return 0

        if getattr(args, "json", False):
            print(json.dumps(files, ensure_ascii=False, default=str))
        else:
            print()
            print(f"  ── Archivos de audio en {dest or '.'} ──")
            print()
            for info in files:
                title = info.get("title", info.get("name", "?"))
                artist = info.get("artist", "")
                album = info.get("album", "")
                duration = info.get("duration", 0)
                dur_str = f" ({duration}s)" if duration else ""
                parts = []
                if artist:
                    parts.append(f"  {artist} - {title}{dur_str}")
                else:
                    parts.append(f"  {title}{dur_str}")
                if album:
                    parts[-1] += f" [{album}]"
                print(parts[-1])
            print()
            print(f"  Total: {len(files)} archivo(s)")

        return 0

    else:
        print(f"Unknown playlist command: {cmd}", file=sys.stderr)
        return 1
