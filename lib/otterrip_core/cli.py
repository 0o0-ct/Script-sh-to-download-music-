#!/usr/bin/env python3
"""Typer CLI for OtterRip.

Modern CLI interface with autocompletion, Rich output, and clear help.
This module wraps the existing argparse-based subcommands in Typer
for a better user experience while maintaining backward compatibility.

Usage:
    python -m otterrip_core.cli
    otterrip  (if installed)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

try:
    import typer
    from typing import Annotated
    HAS_TYPER = True
except ImportError:
    HAS_TYPER = False
    typer = None
    Annotated = None

# Import rich_output for styled output
from . import rich_output as ro

# ── App definition ───────────────────────────────────────────────────

if HAS_TYPER:
    app = typer.Typer(
        name="otterrip",
        add_completion=True,
        pretty_exceptions_short=True,
        help="OtterRip — Download, organize and tag music intelligently.",
    )
else:
    app = None

# ── Autocompletion helpers ───────────────────────────────────────────

def _complete_urls(incomplete: str) -> list[str]:
    """Complete URLs from history."""
    # Could read from state_db history for autocompletion
    return []


# ── Download commands ────────────────────────────────────────────────

@app.command("download")
def download(
    url: Annotated[str, typer.Argument(help="YouTube URL to download")],
    dest: Annotated[Optional[str], typer.Option("--dest", "-d", help="Destination directory")] = None,
    workers: Annotated[int, typer.Option("--workers", "-w", help="Parallel workers")] = 2,
    profile: Annotated[str, typer.Option("--profile", "-p", help="Audio profile")] = "original",
    normalize: Annotated[bool, typer.Option("--normalize", help="Normalize volume")] = False,
    lyrics: Annotated[bool, typer.Option("--lyrics", help="Fetch lyrics")] = True,
    metadata: Annotated[bool, typer.Option("--metadata", help="Fix metadata")] = True,
    json: Annotated[bool, typer.Option("--json", help="JSON output")] = False,
):
    """Download music from a YouTube URL.

    Downloads audio from YouTube, applies the specified audio profile,
    optionally fetches lyrics and fixes metadata using MusicBrainz.
    """
    _run_py_core("download", "engine", url,
                 dest=dest, workers=workers, profile=profile,
                 normalize=normalize, lyrics=lyrics, metadata=metadata,
                 json_output=json)


@app.command("queue")
def queue(
    action: Annotated[str, typer.Argument(help="Queue action: add, list, claim, finish, pause, resume, cancel, retry, clear-done")],
    ids: Annotated[Optional[list[str]], typer.Argument(help="Task IDs or 'all'")] = None,
    url: Annotated[Optional[str], typer.Option("--url", "-u", help="URL(s) for 'add' action")] = None,
    summary: Annotated[Optional[str], typer.Option("--summary", "-s", help="Summary for 'finish' action")] = None,
    dest: Annotated[Optional[str], typer.Option("--dest", "-d", help="Destination directory for 'add'")] = None,
):
    """Manage the download queue.

    Actions: add, list, claim, finish, pause, resume, cancel, retry, clear-done, stats, recover.
    """
    args = [action]
    if ids:
        args.extend(ids)
    if url:
        args.extend(["--url", url])
    if summary:
        args.extend(["--summary", summary])
    if dest:
        args.extend(["--dest", dest])
    _run_py_core("queue", *args)


@app.command("library")
def library(
    action: Annotated[str, typer.Argument(help="Library action: report, scan")] = "report",
    dest: Annotated[Optional[str], typer.Option("--dest", "-d", help="Library directory")] = None,
    json: Annotated[bool, typer.Option("--json", help="JSON output")] = False,
    find_duplicates: Annotated[bool, typer.Option("--find-duplicates", help="Find acoustic duplicates")] = False,
    albums: Annotated[bool, typer.Option("--albums", help="Show incomplete albums")] = False,
    missing_covers: Annotated[bool, typer.Option("--missing-covers", help="Show missing covers")] = False,
    missing_lyrics: Annotated[bool, typer.Option("--missing-lyrics", help="Show missing lyrics")] = False,
):
    """Scan and report on the music library.

    Detects incomplete tags, missing covers, lyrics mismatches,
    and exact or acoustic duplicates.
    """
    args = [action]
    if dest:
        args.extend(["--dest", dest])
    if json:
        args.append("--json")
    if find_duplicates:
        args.append("--find-duplicates")
    if albums:
        args.append("--albums")
    if missing_covers:
        args.append("--missing-covers")
    if missing_lyrics:
        args.append("--missing-lyrics")
    _run_py_core("library", *args)


# ── Playlist commands ────────────────────────────────────────────────

@app.command("playlist")
def playlist(
    action: Annotated[str, typer.Argument(help="Playlist action: export, sync, list")],
    url: Annotated[Optional[str], typer.Option("--url", "-u", help="YouTube playlist URL")] = None,
    dest: Annotated[Optional[str], typer.Option("--dest", "-d", help="Destination directory")] = None,
    json: Annotated[bool, typer.Option("--json", help="JSON output")] = False,
):
    """Manage playlists: export to M3U, sync with local folder.

    Export a YouTube playlist to M3U format, sync a remote playlist
    with a local directory, or list local audio files.
    """
    args = [action]
    if url:
        args.extend(["--url", url])
    if dest:
        args.extend(["--dest", dest])
    if json:
        args.append("--json")
    _run_py_core("playlist", *args)


# ── Organize commands ────────────────────────────────────────────────

@app.command("organize")
def organize(
    dest: Annotated[str, typer.Argument(help="Directory to organize")],
    template: Annotated[Optional[str], typer.Option("--template", "-t", help="Path template")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Preview only")] = False,
    clean: Annotated[bool, typer.Option("--clean", help="Remove empty dirs")] = False,
):
    """Reorganize audio files using customizable templates.

    Default template: {artist}/{album}/{track} - {title}

    Supports dry-run mode to preview changes before applying.
    """
    args = [dest]
    if template:
        args.extend(["--template", template])
    if dry_run:
        args.append("--dry-run")
    if clean:
        args.append("--clean")
    _run_py_core("organize", *args)


# ── Subscriptions commands ───────────────────────────────────────────

@app.command("subscriptions")
def subscriptions(
    action: Annotated[str, typer.Argument(help="Action: add, remove, list, check, timer")],
    url: Annotated[Optional[str], typer.Option("--url", "-u", help="Playlist URL for 'add'")] = None,
    interval: Annotated[Optional[int], typer.Option("--interval", "-i", help="Check interval in hours")] = None,
    dest: Annotated[Optional[str], typer.Option("--dest", "-d", help="Destination directory")] = None,
    systemd: Annotated[bool, typer.Option("--systemd", help="Generate systemd timer")] = False,
):
    """Auto-subscribe to playlists and check for new songs.

    Watch playlists periodically for new songs and download them
    automatically. On Linux, integrates with systemd user timers.
    """
    args = [action]
    if url:
        args.extend(["--url", url])
    if interval:
        args.extend(["--interval", str(interval)])
    if dest:
        args.extend(["--dest", dest])
    if systemd:
        args.append("--systemd")
    _run_py_core("subscriptions", *args)


# ── Metadata commands ────────────────────────────────────────────────

@app.command("fingerprint")
def fingerprint(
    audio_file: Annotated[str, typer.Argument(help="Audio file to fingerprint")],
    acoustid: Annotated[bool, typer.Option("--acoustid", help="Lookup on AcoustID")] = False,
):
    """Compute acoustic fingerprint of an audio file.

    Uses Chromaprint when available, falls back to librosa.
    Optionally looks up the fingerprint on AcoustID.
    """
    args = [audio_file]
    if acoustid:
        args.append("--acoustid")
    _run_py_core("fingerprint", *args)


@app.command("cover-art")
def cover_art(
    release_id: Annotated[str, typer.Argument(help="MusicBrainz release ID")],
    size: Annotated[int, typer.Option("--size", "-s", help="Image size")] = 250,
    cache: Annotated[bool, typer.Option("--cache", "-c", help="Cache the cover")] = True,
    embed: Annotated[Optional[str], typer.Option("--embed", "-e", help="Embed in audio file")] = None,
):
    """Fetch album cover art from Cover Art Archive.

    Downloads the cover for a MusicBrainz release ID and optionally
    caches it or embeds it in an audio file.
    """
    args = [release_id]
    args.extend(["--size", str(size)])
    if cache:
        args.append("--cache")
    if embed:
        args.extend(["--embed", embed])
    _run_py_core("cover-art", *args)


@app.command("mb-lookup")
def mb_lookup(
    title: Annotated[str, typer.Argument(help="Track title")],
    artist: Annotated[str, typer.Argument(help="Artist name")],
    isrc: Annotated[Optional[str], typer.Option("--isrc", help="ISRC code")] = None,
    json: Annotated[bool, typer.Option("--json", help="JSON output")] = False,
):
    """Search MusicBrainz for track metadata.

    Looks up a track by title and artist, or by ISRC code.
    Returns extended metadata including track number, disc number,
    year, label, release ID, and artist IDs.
    """
    args = [title, artist]
    if isrc:
        args.extend(["--isrc", isrc])
    if json:
        args.append("--json")
    _run_py_core("mb-lookup", *args)


# ── Normalize command ────────────────────────────────────────────────

@app.command("normalize")
def normalize_cmd(
    audio_file: Annotated[str, typer.Argument(help="Audio file to normalize")],
    mode: Annotated[str, typer.Option("--mode", "-m", help="Mode: replaygain or loudnorm")] = "replaygain",
    target: Annotated[float, typer.Option("--target", "-t", help="Target loudness (LUFS)")] = -16.0,
):
    """Normalize audio volume with ReplayGain or EBU R128 loudnorm.

    Computes ReplayGain tags or applies two-pass EBU R128 loudness
    normalization to an audio file.
    """
    args = [audio_file, "--mode", mode, "--target", str(target)]
    _run_py_core("normalize", *args)


# ── Post-process command ─────────────────────────────────────────────

@app.command("post-process")
def post_process(
    audio_file: Annotated[str, typer.Argument(help="Audio file to post-process")],
    title: Annotated[Optional[str], typer.Option("--title", help="Track title")] = None,
    artist: Annotated[Optional[str], typer.Option("--artist", help="Artist name")] = None,
    album: Annotated[Optional[str], typer.Option("--album", help="Album name")] = None,
    keep_thumbnail: Annotated[bool, typer.Option("--keep-thumbnail", help="Keep video thumbnail")] = False,
):
    """Post-process audio: fix metadata, embed cover and lyrics.

    Full pipeline: MusicBrainz lookup → fix metadata → Cover Art Archive
    → lyrics embed. Optionally keeps the original video thumbnail.
    """
    args = [audio_file]
    if title:
        args.extend(["--title", title])
    if artist:
        args.extend(["--artist", artist])
    if album:
        args.extend(["--album", album])
    if keep_thumbnail:
        args.append("--keep-thumbnail")
    _run_py_core("post-process", *args)


# ── Dashboard command ────────────────────────────────────────────────

@app.command("dashboard")
def dashboard(
    refresh: Annotated[int, typer.Option("--refresh", "-r", help="Refresh interval (seconds)")] = 5,
):
    """Launch the interactive TUI dashboard.

    Shows active downloads, queue status, and library overview
    in a real-time terminal interface powered by Textual.
    """
    try:
        from .dashboard import DashboardApp
        app_instance = DashboardApp(refresh_interval=refresh)
        app_instance.run()
    except ImportError:
        ro.print_warning("Textual no está instalado. Instala con: pip install textual")
        ro.print_info("Usa 'otterrip queue list' o 'otterrip library report' como alternativa.")


# ── Doctor command ───────────────────────────────────────────────────

@app.command("doctor")
def doctor(
    json: Annotated[bool, typer.Option("--json", help="JSON output")] = False,
):
    """Run diagnostics and system checks.

    Checks for required dependencies (yt-dlp, ffmpeg, mutagen),
    optional dependencies (chromaprint, pyacoustid, textual),
    and configuration issues.
    """
    _run_py_core("doctor", json_output=json)


# ── Yt-dlp management ────────────────────────────────────────────────

@app.command("ytdlp")
def ytdlp_cmd(
    action: Annotated[str, typer.Argument(help="Action: version, update-check, update, rollback")],
):
    """Manage yt-dlp version and updates.

    Check current version, verify updates, update with backup,
    or rollback to a previous version.
    """
    _run_py_core("ytdlp", action)


# ── State DB command ─────────────────────────────────────────────────

@app.command("state-db")
def state_db(
    action: Annotated[str, typer.Argument(help="Action: history, lyrics-cache, metadata-cache, migrate, backup")],
    sub_action: Annotated[Optional[str], typer.Argument(help="Sub-action: get, set, clear, list")] = None,
    key: Annotated[Optional[str], typer.Option("--key", "-k", help="Cache key")] = None,
):
    """Manage the OtterRip state database.

    Access history, lyrics cache, metadata cache, trigger migration
    from JSON/TSV, or create backups.
    """
    args = [action]
    if sub_action:
        args.append(sub_action)
    if key:
        args.extend(["--key", key])
    _run_py_core("state-db", *args)


# ── Version command ──────────────────────────────────────────────────

@app.command("version")
def version():
    """Show OtterRip version."""
    _run_py_core("version")


# ── Helper: run py_core subcommand ───────────────────────────────────

def _run_py_core(*args: str, **kwargs: str) -> int:
    """Dispatch to the argparse-based __main__.py handler.

    This keeps Typer as a thin wrapper around existing logic.
    """
    # Build command line for __main__.py
    main_module = Path(__file__).parent / "__main__.py"
    cmd_args = list(args)

    # Add keyword args as --key value pairs
    for key, value in kwargs.items():
        key_arg = "--" + key.replace("_", "-")
        if value is True:
            cmd_args.append(key_arg)
        elif value is not None:
            cmd_args.append(key_arg)
            cmd_args.append(str(value))

    if not cmd_args:
        ro.get_console().print("No action specified. Use --help for usage.")
        return 1

    # Import and call the main handler directly
    try:
        from . import __main__ as main_mod
        # Parse and dispatch
        sys.argv = ["otterrip"] + cmd_args
        main_mod.main()
        return 0
    except SystemExit as e:
        return e.code if e.code is not None else 1
    except Exception as e:
        ro.print_error(f"Error: {e}")
        return 1


# ── Entry point ──────────────────────────────────────────────────────

def main():
    """Entry point for the Typer CLI."""
    if not HAS_TYPER:
        print("Typer no está instalado. Instala con: pip install typer", file=sys.stderr)
        sys.exit(1)
    app()


if __name__ == "__main__":
    main()
