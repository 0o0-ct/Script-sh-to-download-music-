#!/usr/bin/env python3
"""OtterRip Core CLI entry-point.

Dispatches subcommands invoked from the Bash wrapper via:
    python3 -m otterrip_core <subcommand> [args...]

Available subcommands:
    fix-metadata, lyrics, lyrics-tool, verify-session, clean-names,
    merge-sessions, classify-log, doctor, queue, library, repair, convert
"""

import argparse
import sys

from otterrip_core import (
    classify_log,
    clean_names,
    convert,
    detect_url,
    doctor,
    download_engine,
    fix_metadata,
    library,
    lyrics,
    lyrics_overrides,
    lyrics_tool,
    lyrics_verify,
    merge_sessions,
    normalize,
    organize,
    playlist_manager,
    post_process,
    queue,
    repair,
    state_db,
    subscriptions,
    verify_session,
    ytdlp_manager,
)


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="otterrip_core",
        description="OtterRip Python backend",
    )
    sub = parser.add_subparsers(dest="command")

    # --- detect-url ---
    p = sub.add_parser("detect-url", help="Classify a YouTube URL")
    p.add_argument("url", help="URL to classify")
    p.add_argument("--ytdlp", default="", help="Path to yt-dlp binary")
    p.add_argument("--js-args", default="", help="JS runtime args")
    p.add_argument("--cookies-file", default="")
    p.add_argument("--cookies-browser", default="")
    p.add_argument("--no-playlist", action="store_true")
    p.add_argument("--timeout", type=int, default=50)

    # --- doctor ---
    p = sub.add_parser("doctor", help="System diagnostics")
    p.add_argument("--dest", default="")

    # --- fix-metadata ---
    p = sub.add_parser("fix-metadata", help="Fix audio metadata")
    p.add_argument("target")
    p.add_argument("fmt")
    p.add_argument("--session", default="")
    p.add_argument("--cover", default="")

    # --- lyrics ---
    p = sub.add_parser("lyrics", help="Search and embed lyrics")
    p.add_argument("target")
    p.add_argument("fmt")
    p.add_argument("--host", default="https://lrclib.net")
    p.add_argument("--mode", default="embed")
    p.add_argument("--providers", default="lrclib,multiweb")
    p.add_argument("--synced-dir", default="")
    p.add_argument("--lang", default="")
    p.add_argument("--plain-ok", action="store_true")
    p.add_argument("--upgrade-plain", action="store_true")
    p.add_argument("--sync-level", default="line", choices=["line", "word", "auto"])
    p.add_argument("--sidecar", action="store_true", default=False)
    p.add_argument("--embed-style", default="lrc", choices=["lrc", "plain"])
    p.add_argument("--min-confidence", type=float, default=0.0)
    p.add_argument("--offset-ms", type=int, default=0)
    p.add_argument("--auto-align", action="store_true", default=False)
    p.add_argument("--session", default="")
    p.add_argument("--summary", default="")
    p.add_argument("--overwrite", action="store_true")
    p.add_argument("--jobs", type=positive_int, default=3)

    # --- lyrics-tool ---
    p = sub.add_parser("lyrics-tool", help="Lyrics utility commands")
    lt_sub = p.add_subparsers(dest="lyrics_tool_cmd")

    lt_compare = lt_sub.add_parser("compare", help="Compare provider results")
    lt_compare.add_argument("target")
    lt_compare.add_argument("--host", default="https://lrclib.net")
    lt_compare.add_argument("--mode", default="embed")
    lt_compare.add_argument("--providers", default="lrclib,multiweb")
    lt_compare.add_argument("--synced-dir", default="")
    lt_compare.add_argument("--lang", default="")
    lt_compare.add_argument("--plain-ok", action="store_true")
    lt_compare.add_argument("--upgrade-plain", action="store_true")

    lt_show = lt_sub.add_parser("show", help="Show embedded lyrics")
    lt_show.add_argument("target")

    lt_shift = lt_sub.add_parser("shift", help="Shift LRC timestamps")
    lt_shift.add_argument("target")
    lt_shift.add_argument("ms", type=int)

    lt_convert = lt_sub.add_parser("convert", help="Convert LRC to TXT")
    lt_convert.add_argument("target")

    lt_set = lt_sub.add_parser("set", help="Embed lyrics file into audio")
    lt_set.add_argument("target")
    lt_set.add_argument("source")

    # --- verify-session ---
    p = sub.add_parser("verify-session", help="Verify downloaded files")
    p.add_argument("--session", required=True)
    p.add_argument("--ext", required=True)
    p.add_argument("--dest", required=True)
    p.add_argument("--archive", required=True)
    p.add_argument("--out-ok", required=True)
    p.add_argument("--out-failed", required=True)

    # --- clean-names ---
    p = sub.add_parser(
        "clean-names",
        help="Remove YouTube noise from downloaded filenames",
    )
    p.add_argument("target", help="Destination folder to clean")
    p.add_argument(
        "--manifest",
        action="append",
        default=[],
        help="JSON-line session manifest to rewrite after renaming",
    )
    p.add_argument("--dry-run", action="store_true", default=False)

    # --- merge-sessions ---
    p = sub.add_parser("merge-sessions", help="Merge session files")
    p.add_argument("output")
    p.add_argument("inputs", nargs="+")

    # --- classify-log ---
    p = sub.add_parser("classify-log", help="Classify yt-dlp log errors")
    p.add_argument("logfiles", nargs="+")

    # --- queue ---
    p = sub.add_parser("queue", help="Download queue management")
    q_sub = p.add_subparsers(dest="queue_cmd")
    q_sub.add_parser("recover")
    p_claim = q_sub.add_parser("claim")
    p_claim.add_argument("--pid", type=int, required=True)
    p_get = q_sub.add_parser("get")
    p_get.add_argument("task_id")
    p_get.add_argument("--field", required=True)
    p_finish = q_sub.add_parser("finish")
    p_finish.add_argument("task_id")
    p_finish.add_argument("status")
    p_finish.add_argument("--error", default="")
    p_finish.add_argument("--summary", default="")
    p_add = q_sub.add_parser("add")
    p_add.add_argument("urls", nargs="+")
    p_add.add_argument("--dest", default="")
    q_sub.add_parser("list")
    q_sub.add_parser("stats")
    p_rm = q_sub.add_parser("rm")
    p_rm.add_argument("task_id")
    q_sub.add_parser("clear")
    p_pause = q_sub.add_parser("pause")
    p_pause.add_argument("ids", nargs="+")
    p_resume = q_sub.add_parser("resume")
    p_resume.add_argument("ids", nargs="+")
    p_cancel = q_sub.add_parser("cancel")
    p_cancel.add_argument("ids", nargs="+")
    p_retry = q_sub.add_parser("retry")
    p_retry.add_argument("ids", nargs="+")
    q_sub.add_parser("clear-done")
    p_renew = q_sub.add_parser("renew-lease")
    p_renew.add_argument("task_id")
    p_renew.add_argument("--lease-seconds", type=int, default=300)

    # --- state-db (CLI bridge for SQLite backend) ---
    p_sdb = sub.add_parser("state-db", help="State database operations")
    sdb_sub = p_sdb.add_subparsers(dest="state_db_cmd")

    # state-db history
    p_sdb_hist = sdb_sub.add_parser("history")
    hist_sub = p_sdb_hist.add_subparsers(dest="history_cmd")
    p_hist_append = hist_sub.add_parser("append")
    p_hist_append.add_argument("url")
    p_hist_append.add_argument("title")
    p_hist_append.add_argument("dest")
    p_hist_append.add_argument("status")
    p_hist_append.add_argument("duration", type=int, default=0)
    p_hist_list = hist_sub.add_parser("list")
    p_hist_list.add_argument("--max", type=int, default=50)
    p_hist_clear = hist_sub.add_parser("clear")
    p_hist_clear.add_argument("--mode", default="all")
    hist_sub.add_parser("count")

    # state-db lyrics-cache
    p_sdb_lrc = sdb_sub.add_parser("lyrics-cache")
    lrc_sub = p_sdb_lrc.add_subparsers(dest="lyrics_cache_cmd")
    p_lrc_get = lrc_sub.add_parser("get")
    p_lrc_get.add_argument("key")
    p_lrc_set = lrc_sub.add_parser("set")
    p_lrc_set.add_argument("key")
    p_lrc_set.add_argument("bundle_json")
    p_lrc_set.add_argument("--keyed-by", default="legacy")
    lrc_sub.add_parser("clear")

    # state-db metadata-cache
    p_sdb_meta = sdb_sub.add_parser("metadata-cache")
    meta_sub = p_sdb_meta.add_subparsers(dest="metadata_cache_cmd")
    p_meta_get = meta_sub.add_parser("get")
    p_meta_get.add_argument("key")
    p_meta_set = meta_sub.add_parser("set")
    p_meta_set.add_argument("key")
    p_meta_set.add_argument("data_json")
    meta_sub.add_parser("clear")

    # state-db migrate
    sdb_sub.add_parser("migrate")

    # state-db backup
    sdb_sub.add_parser("backup")

    # --- library ---
    p = sub.add_parser("library", help="Music library tools")
    lib_sub = p.add_subparsers(dest="library_cmd")
    p_report = lib_sub.add_parser("report")
    p_report.add_argument("path")
    p_report.add_argument("--save", action="store_true")
    p_report.add_argument("--json", action="store_true", help="Output as JSON")
    p_report.add_argument("--find-duplicates", action="store_true", help="Find acoustic duplicates")
    p_report.add_argument("--albums", action="store_true", help="Check incomplete albums")
    p_report.add_argument("--missing-covers", action="store_true", help="List files without covers")
    p_report.add_argument("--missing-lyrics", action="store_true", help="List files without lyrics")

    # --- playlist ---
    p = sub.add_parser("playlist", help="Playlist management")
    pl_sub = p.add_subparsers(dest="playlist_cmd")
    p_export = pl_sub.add_parser("export", help="Export playlist to M3U")
    p_export.add_argument("url", help="YouTube playlist URL")
    p_export.add_argument("--dest", required=True, help="Destination directory")
    p_sync = pl_sub.add_parser("sync", help="Sync playlist with local folder")
    p_sync.add_argument("url", help="YouTube playlist URL")
    p_sync.add_argument("--dest", required=True, help="Local directory")
    p_sync.add_argument("--json", action="store_true", help="Output as JSON")
    p_list = pl_sub.add_parser("list", help="List audio files in directory")
    p_list.add_argument("--dest", default="", help="Directory to list")
    p_list.add_argument("--json", action="store_true", help="Output as JSON")

    # --- organize ---
    p = sub.add_parser("organize", help="Reorganize music files")
    p.add_argument("path", help="Directory to organize")
    p.add_argument(
        "--template",
        default="{artist}/{album}/{track} - {title}",
        help="Organization template (default: {artist}/{album}/{track} - {title})",
    )
    p.add_argument("--dry-run", action="store_true", default=True, help="Preview changes without moving files")
    p.add_argument("--apply", action="store_true", help="Apply changes (move files)")
    p.add_argument("--json", action="store_true", help="Output as JSON")

    # --- subscriptions ---
    p = sub.add_parser("subscriptions", help="Playlist subscriptions")
    sub_sub = p.add_subparsers(dest="subscriptions_cmd")
    p_add = sub_sub.add_parser("add", help="Add a subscription")
    p_add.add_argument("url", help="YouTube playlist URL")
    p_add.add_argument("--interval", type=int, default=86400, help="Check interval in seconds (default: 86400)")
    p_rm = sub_sub.add_parser("remove", help="Remove a subscription")
    p_rm.add_argument("url", help="URL to remove")
    p_sub_list = sub_sub.add_parser("list", help="List subscriptions")
    p_sub_list.add_argument("--json", action="store_true", help="Output as JSON")
    p_check = sub_sub.add_parser("check", help="Check subscriptions for new songs")
    p_check.add_argument("--url", default="", help="Check specific URL")
    p_check.add_argument("--download", action="store_true", help="Download new songs")
    p_check.add_argument("--json", action="store_true", help="Output as JSON")
    p_timer = sub_sub.add_parser("timer", help="Configure systemd timer")
    p_timer.add_argument("--enable", action="store_true", default=True, help="Enable the timer (default)")
    p_timer.add_argument("--disable", action="store_true", help="Disable the timer")

    # --- repair ---
    p = sub.add_parser("repair", help="Repair metadata")
    p.add_argument("target")
    p.add_argument("--apply", action="store_true")
    p.add_argument("--clean-noise", action="store_true")

    # --- convert ---
    p = sub.add_parser("convert", help="Convert audio files")
    p.add_argument("source")
    p.add_argument("--to", required=True)
    p.add_argument("--dest", required=True)
    p.add_argument("--quality", default="")

    # --- lyrics-manage (verify / repair / shift) ---
    p = sub.add_parser("lyrics-manage", help="Lyrics library management")
    lm_sub = p.add_subparsers(dest="lyrics_manage_cmd")

    p_verify = lm_sub.add_parser("verify", help="Verify lyrics in a directory")
    p_verify.add_argument("path")

    p_repair = lm_sub.add_parser("repair", help="Repair lyrics in a directory")
    p_repair.add_argument("path")
    p_repair.add_argument("--dry-run", action="store_true", default=False)
    p_repair.add_argument("--lang", default="")

    p_shift = lm_sub.add_parser("shift", help="Shift LRC timestamps (tag + sidecar)")
    p_shift.add_argument("file")
    p_shift.add_argument("ms", type=int)
    p_shift.add_argument("--lang", default="")

    # --- lyrics-overrides (interactive offset / candidate selection) ---
    p = sub.add_parser("lyrics-overrides", help="Lyrics overrides management")
    lo_sub = p.add_subparsers(dest="lyrics_overrides_cmd")

    lo_get = lo_sub.add_parser("get", help="Get stored offset for a file")
    lo_get.add_argument("file")

    lo_set = lo_sub.add_parser("set", help="Set offset override for a file")
    lo_set.add_argument("file")
    lo_set.add_argument("--offset", type=int, required=True)

    lo_interactive = lo_sub.add_parser("interactive", help="Interactive offset adjustment")
    lo_interactive.add_argument("file")

    lo_select = lo_sub.add_parser("select", help="Select from lyric candidates")
    lo_select.add_argument("file")
    lo_select.add_argument("--host", default="https://lrclib.net")
    lo_select.add_argument("--providers", default="lrclib,multiweb")
    lo_select.add_argument("--lang", default="")

    lo_list = lo_sub.add_parser("list", help="List all stored overrides")

    # --- fingerprint ---
    p = sub.add_parser("fingerprint", help="Compute acoustic fingerprint")
    p.add_argument("audio_file", help="Path to audio file")
    p.add_argument("--acoustid", action="store_true", help="Look up on AcoustID")

    # --- cover-art ---
    p = sub.add_parser("cover-art", help="Download album cover art")
    p.add_argument("release_id", help="MusicBrainz release MBID")
    p.add_argument("--size", default="250", choices=["thumbnail", "250", "500", "archive"])
    p.add_argument("--cache", action="store_true", help="Cache the result")
    p.add_argument("--embed", default="", help="Embed in this audio file")

    # --- mb-lookup ---
    p = sub.add_parser("mb-lookup", help="Search MusicBrainz for metadata")
    p.add_argument("title", help="Recording title")
    p.add_argument("artist", help="Artist name")
    p.add_argument("--album", default="", help="Album name (optional)")
    p.add_argument("--isrc", default="", help="Search by ISRC instead")

    # --- normalize ---
    p = sub.add_parser("normalize", help="Normalize audio volume")
    p.add_argument("target", help="Audio file or directory")
    p.add_argument(
        "--mode",
        default="replaygain",
        choices=["replaygain", "loudnorm"],
        help="Normalization mode (default: replaygain)",
    )
    p.add_argument("--target-lufs", type=float, default=-16.0, help="Target integrated loudness in LUFS (default: -16)")
    p.add_argument("--true-peak", type=float, default=-1.5, help="True peak limit in dBTP (default: -1.5)")

    # --- post-process ---
    p = sub.add_parser("post-process", help="Post-processing pipeline")
    p.add_argument("target", help="Audio file or directory")
    p.add_argument("--keep-thumbnail", action="store_true", help="Keep video thumbnail instead of fetching cover art")
    p.add_argument("--session", default="", help="Session manifest file (JSON lines)")

    # --- download engine ---
    p = sub.add_parser("download", help="Download engine using yt-dlp API")
    dl_sub = p.add_subparsers(dest="download_cmd")
    dl_engine = dl_sub.add_parser("engine", help="Download using Python engine")
    dl_engine.add_argument("url", help="URL to download")
    dl_engine.add_argument("--dest", required=True, help="Destination directory")
    dl_engine.add_argument("--format", default="mp3", help="Audio format profile")
    dl_engine.add_argument("--workers", type=int, default=3, help="Parallel workers (1-32)")
    dl_engine.add_argument("--archive", default="", help="Download archive file")
    dl_engine.add_argument("--quality", default="320K", help="Audio quality/bitrate")
    dl_engine.add_argument("--dur-min", type=int, default=60, help="Minimum duration in seconds")
    dl_engine.add_argument("--dur-max", type=int, default=480, help="Maximum duration in seconds")
    dl_engine.add_argument("--no-lyrics", action="store_true", help="Disable lyrics fetching")
    dl_engine.add_argument("--proxy", default="", help="Proxy URL")
    dl_engine.add_argument("--cookies-from-browser", default="", help="Browser for cookies")
    dl_engine.add_argument("--cookies", default="", help="Cookies file path")

    # --- ytdlp ---
    p = sub.add_parser("ytdlp", help="yt-dlp version management")
    yt_sub = p.add_subparsers(dest="ytdlp_cmd")
    yt_sub.add_parser("version", help="Show installed yt-dlp version")
    yt_sub.add_parser("update-check", help="Check for yt-dlp updates")
    yt_sub.add_parser("update", help="Update yt-dlp with backup")
    yt_sub.add_parser("rollback", help="Rollback to backup version")

    # --- dashboard ---
    p = sub.add_parser("dashboard", help="Interactive TUI dashboard")
    p.add_argument("--refresh", type=int, default=5, help="Refresh interval in seconds")

    return parser


COMMAND_MAP = {
    "detect-url": detect_url.run_detect_url,
    "doctor": doctor.run_doctor,
    "fix-metadata": fix_metadata.run_fix_metadata,
    "lyrics": lyrics.run_lyrics,
    "lyrics-tool": lyrics_tool.run_lyrics_tool,
    "verify-session": verify_session.run_verify_session,
    "clean-names": clean_names.run_clean_names,
    "merge-sessions": merge_sessions.run_merge_sessions,
    "classify-log": classify_log.run_classify_log,
    "queue": queue.run_queue,
    "library": library.run_library,
    "repair": repair.run_repair,
    "convert": convert.run_convert,
    "lyrics-manage": lyrics_verify.run_lyrics_manage,
    "lyrics-overrides": lyrics_overrides.run_lyrics_overrides,
    "state-db": None,  # handled inline
    "fingerprint": None,  # handled inline
    "cover-art": None,  # handled inline
    "mb-lookup": None,  # handled inline
    "normalize": normalize.run_normalize,
    "post-process": post_process.run_post_process,
    "download": None,  # handled inline
    "ytdlp": None,  # handled inline
    "playlist": playlist_manager.run_playlist,
    "organize": organize.run_organize,
    "subscriptions": subscriptions.run_subscriptions,
    "dashboard": None,  # handled inline
}


def _run_state_db(args) -> int:
    """Handle state-db subcommands."""
    import json as _json
    import os as _os
    import shutil as _shutil

    cmd = getattr(args, "state_db_cmd", None)
    if not cmd:
        print("Usage: state-db {history|lyrics-cache|metadata-cache|migrate|backup}")
        return 1

    state_dir = _os.environ.get("OTTERRIP_STATE_DIR", "")
    if not state_dir:
        print("Error: OTTERRIP_STATE_DIR not set", file=sys.stderr)
        return 1

    db = state_db.StateDB(state_dir)

    if cmd == "history":
        sub = getattr(args, "history_cmd", None)
        if sub == "append":
            rid = db.history_append(args.url, args.title, args.dest, args.status, args.duration)
            print(rid)
            return 0
        elif sub == "list":
            entries = db.history_list(max_rows=args.max)
            for e in entries:
                print(_json.dumps(e, ensure_ascii=False))
            return 0
        elif sub == "clear":
            count = db.history_clear(mode=args.mode)
            print(count)
            return 0
        elif sub == "count":
            print(db.history_count())
            return 0
        else:
            print("Usage: state-db history {append|list|clear|count}")
            return 1

    elif cmd == "lyrics-cache":
        sub = getattr(args, "lyrics_cache_cmd", None)
        if sub == "get":
            result = db.lyrics_cache_get(args.key)
            if result is not None:
                print(_json.dumps(result, ensure_ascii=False))
                return 0
            return 1
        elif sub == "set":
            bundle = _json.loads(args.bundle_json)
            keyed_by = getattr(args, "keyed_by", "legacy") or "legacy"
            db.lyrics_cache_set(args.key, bundle, keyed_by=keyed_by)
            return 0
        elif sub == "clear":
            count = db.lyrics_cache_clear()
            print(count)
            return 0
        else:
            print("Usage: state-db lyrics-cache {get|set|clear}")
            return 1

    elif cmd == "metadata-cache":
        sub = getattr(args, "metadata_cache_cmd", None)
        if sub == "get":
            result = db.metadata_cache_get(args.key)
            if result is not None:
                print(_json.dumps(result, ensure_ascii=False))
                return 0
            return 1
        elif sub == "set":
            data = _json.loads(args.data_json)
            db.metadata_cache_set(args.key, data)
            return 0
        elif sub == "clear":
            count = db.metadata_cache_clear()
            print(count)
            return 0
        else:
            print("Usage: state-db metadata-cache {get|set|clear}")
            return 1

    elif cmd == "migrate":
        # Migration runs automatically on StateDB init; this is a no-op trigger
        print("Migration already applied on init.")
        return 0

    elif cmd == "backup":
        db_path = _os.path.join(state_dir, "state.db")
        if not _os.path.exists(db_path):
            print("No state.db to backup.", file=sys.stderr)
            return 1
        import time as _time

        ts = _time.strftime("%Y%m%d_%H%M%S")
        backup_path = f"{db_path}.bak.{ts}"
        _shutil.copy2(db_path, backup_path)
        print(f"Backup created: {backup_path}")
        return 0

    else:
        print(f"Unknown state-db command: {cmd}", file=sys.stderr)
        return 1


def _run_download(args) -> int:
    """Handle download subcommands."""
    cmd = getattr(args, "download_cmd", None)
    if not cmd:
        print("Usage: download engine <url> --dest <dir>")
        return 1

    if cmd == "engine":
        archive = getattr(args, "archive", "") or ""
        proxy = getattr(args, "proxy", "") or ""
        cookies_browser = getattr(args, "cookies-from-browser", "") or ""
        cookies_file = getattr(args, "cookies", "") or ""

        engine = download_engine.DownloadEngine(
            dest=args.dest,
            format_profile=getattr(args, "format", "mp3") or "mp3",
            workers=getattr(args, "workers", 3) or 3,
            archive_path=archive if archive else None,
            quality=getattr(args, "quality", "320K") or "320K",
            dur_min=getattr(args, "dur_min", 60) or 60,
            dur_max=getattr(args, "dur_max", 480) or 480,
            fetch_lyrics=not getattr(args, "no_lyrics", False),
            proxy=proxy if proxy else None,
            cookies_from_browser=cookies_browser if cookies_browser else None,
            cookies_file=cookies_file if cookies_file else None,
        )

        try:
            summary = engine.download(args.url)
        except KeyboardInterrupt:
            print("\nDescarga cancelada por el usuario.", file=sys.stderr)
            engine.cancel_all()
            return 130

        if summary.errors > 0 and summary.ok == 0:
            return 1
        return 0

    return 1


def _run_ytdlp(args) -> int:
    """Handle ytdlp subcommands."""
    import json as _json

    cmd = getattr(args, "ytdlp_cmd", None)
    if not cmd:
        print("Usage: ytdlp {version|update-check|update|rollback}")
        return 1

    if cmd == "version":
        v = ytdlp_manager.get_version()
        print(f"yt-dlp version: {v or 'not installed'}")
        return 0

    elif cmd == "update-check":
        if ytdlp_manager.check_update_interval():
            available = ytdlp_manager.is_update_available()
            ytdlp_manager._update_last_check()
            current = ytdlp_manager.get_version() or "unknown"
            latest = ytdlp_manager.get_latest_version() or "unknown"
            print(
                _json.dumps(
                    {
                        "installed": current,
                        "latest": latest,
                        "update_available": available,
                    },
                    ensure_ascii=False,
                )
            )
            return 0 if available else 1
        else:
            state = ytdlp_manager._load_state()
            last_check = state.get("last_check", 0)
            import time as _time

            days_left = (ytdlp_manager.UPDATE_CHECK_INTERVAL - (_time.time() - last_check)) / (24 * 60 * 60)
            print(
                _json.dumps(
                    {
                        "update_available": False,
                        "next_check_in_days": round(days_left, 1),
                    },
                    ensure_ascii=False,
                )
            )
            return 1

    elif cmd == "update":
        result = ytdlp_manager.update(backup=True)
        print(_json.dumps(result, ensure_ascii=False))
        return 0 if result["success"] else 1

    elif cmd == "rollback":
        result = ytdlp_manager.rollback()
        print(_json.dumps(result, ensure_ascii=False))
        return 0 if result["success"] else 1

    return 1


def _run_new_commands(args) -> int:
    """Handle fingerprint, cover-art, and mb-lookup subcommands."""
    import json as _json

    cmd = args.command

    if cmd == "fingerprint":
        from otterrip_core.audio_fingerprint import (
            compute_fingerprint,
            compute_fingerprint_acoustid,
        )

        if getattr(args, "acoustid", False):
            result = compute_fingerprint_acoustid(args.audio_file)
            if result:
                print(_json.dumps(result, ensure_ascii=False))
                return 0
            print("No AcoustID match found.", file=sys.stderr)
            return 1
        else:
            fp = compute_fingerprint(args.audio_file)
            if fp:
                print(fp)
                return 0
            print("Could not compute fingerprint.", file=sys.stderr)
            return 1

    elif cmd == "cover-art":
        from otterrip_core.cover_art import (
            embed_cover,
            fetch_and_cache_cover,
        )

        size = getattr(args, "size", "250") or "250"
        do_cache = getattr(args, "cache", False)
        embed_file = getattr(args, "embed", "")

        if do_cache:
            data = fetch_and_cache_cover(args.release_id, size)
        else:
            from otterrip_core.cover_art import fetch_cover_art

            data = fetch_cover_art(args.release_id, size)

        if data is None:
            print("No cover art found.", file=sys.stderr)
            return 1

        if embed_file:
            ok = embed_cover(embed_file, data)
            if ok:
                print(f"Cover embedded in {embed_file}")
            else:
                print("Failed to embed cover.", file=sys.stderr)
                return 1
        else:
            # Write to stdout
            sys.stdout.buffer.write(data)
        return 0

    elif cmd == "mb-lookup":
        from otterrip_core.musicbrainz import (
            lookup_by_isrc_sync,
            lookup_canonical_sync,
        )

        isrc = getattr(args, "isrc", "")
        if isrc:
            meta = lookup_by_isrc_sync(isrc)
        else:
            album = getattr(args, "album", "") or ""
            meta = lookup_canonical_sync(args.title, args.artist, album)

        if meta is None:
            print("No MusicBrainz match found.", file=sys.stderr)
            return 1

        print(_json.dumps(meta.to_dict(), ensure_ascii=False))
        return 0

    return 1


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    handler = COMMAND_MAP.get(args.command)

    # Inline handlers
    if args.command == "state-db":
        return _run_state_db(args)

    if args.command == "download":
        return _run_download(args)

    if args.command == "ytdlp":
        return _run_ytdlp(args)

    if args.command in ("fingerprint", "cover-art", "mb-lookup"):
        return _run_new_commands(args)

    if args.command == "organize":
        # Handle --apply vs --dry-run
        if getattr(args, "apply", False):
            args.dry_run = False
        return organize.run_organize(args)

    if args.command == "subscriptions":
        # Handle --disable flag for timer
        if getattr(args, "subscriptions_cmd", None) == "timer":
            if getattr(args, "disable", False):
                args.enable = False
        return subscriptions.run_subscriptions(args)

    if args.command == "dashboard":
        from otterrip_core.dashboard import run_dashboard

        return run_dashboard(args)

    if handler is None:
        print(f"Unknown command: {args.command}", file=sys.stderr)
        return 1

    try:
        return handler(args)
    except KeyboardInterrupt:
        print("\nInterrupted.")
        return 130
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
