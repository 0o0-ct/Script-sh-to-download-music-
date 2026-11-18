#!/usr/bin/env python3
"""OtterRip Core CLI entry-point.

Dispatches subcommands invoked from the Bash wrapper via:
    python3 -m otterrip_core <subcommand> [args...]

Available subcommands:
    fix-metadata, lyrics, lyrics-tool, verify-session, clean-names,
    merge-sessions, classify-log, doctor, queue, library, repair, convert
"""
import sys
import argparse

from otterrip_core import detect_url
from otterrip_core import doctor
from otterrip_core import fix_metadata
from otterrip_core import lyrics
from otterrip_core import lyrics_tool
from otterrip_core import clean_names
from otterrip_core import verify_session
from otterrip_core import merge_sessions
from otterrip_core import classify_log
from otterrip_core import queue
from otterrip_core import library
from otterrip_core import repair
from otterrip_core import convert
from otterrip_core import lyrics_verify
from otterrip_core import lyrics_overrides


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

    # --- library ---
    p = sub.add_parser("library", help="Music library tools")
    lib_sub = p.add_subparsers(dest="library_cmd")
    p_report = lib_sub.add_parser("report")
    p_report.add_argument("path")
    p_report.add_argument("--save", action="store_true")

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
}


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    handler = COMMAND_MAP.get(args.command)
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
