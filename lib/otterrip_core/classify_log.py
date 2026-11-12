#!/usr/bin/env python3
"""Classify yt-dlp log errors (classify-log command)."""
from __future__ import annotations

import re
from pathlib import Path


# Patterns that indicate each error class
RECOVERABLE_PATTERNS = [
    re.compile(r"ERROR.*Temporary failure", re.I),
    re.compile(r"ERROR.*HTTPError.*5\d\d", re.I),
    re.compile(r"ERROR.*Read timed out", re.I),
    re.compile(r"ERROR.*Connection.*refused", re.I),
    re.compile(r"ERROR.*Too many requests", re.I),
    re.compile(r"ERROR.*retry", re.I),
]

SERVICE_UNAVAILABLE_PATTERNS = [
    re.compile(r"ERROR.*503", re.I),
    re.compile(r"ERROR.*Service Unavailable", re.I),
    re.compile(r"ERROR.*Backend fetch failed", re.I),
    re.compile(r"ERROR.*Origin error", re.I),
    re.compile(r"ERROR.*502", re.I),
]

PERMANENT_PATTERNS = [
    re.compile(r"ERROR.*404", re.I),
    re.compile(r"ERROR.*Video unavailable", re.I),
    re.compile(r"ERROR.*private", re.I),
    re.compile(r"ERROR.*age.*restrict", re.I),
    re.compile(r"ERROR.*403", re.I),
    re.compile(r"ERROR.*Sign in", re.I),
]

MISSING_DEP_PATTERNS = [
    re.compile(r"ERROR.*ffmpeg.*not.*found", re.I),
    re.compile(r"ERROR.*postprocessor.*not.*found", re.I),
    re.compile(r"ERROR.*requires.*installed", re.I),
]


def classify_line(line: str) -> str:
    """Return the error class for a single log line."""
    for pat in MISSING_DEP_PATTERNS:
        if pat.search(line):
            return "missing_dependency"
    for pat in SERVICE_UNAVAILABLE_PATTERNS:
        if pat.search(line):
            return "service_unavailable"
    for pat in PERMANENT_PATTERNS:
        if pat.search(line):
            return "permanent"
    for pat in RECOVERABLE_PATTERNS:
        if pat.search(line):
            return "recoverable"
    return "recoverable"  # default


def run_classify_log(args) -> int:
    """Read yt-dlp log files and output key=value classification lines."""
    counts: dict[str, int] = {
        "recoverable": 0,
        "service_unavailable": 0,
        "permanent": 0,
        "missing_dependency": 0,
    }

    for logfile in args.logfiles:
        p = Path(logfile)
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if not line.startswith("ERROR:"):
                continue
            cls = classify_line(line)
            counts[cls] = counts.get(cls, 0) + 1

    # Output key=value pairs — the Bash wrapper reads specific keys
    for key, val in counts.items():
        print(f"{key}={val}")
    # Overall: worst class wins
    if counts["missing_dependency"]:
        print("overall=missing_dependency")
    elif counts["service_unavailable"]:
        print("overall=service_unavailable")
    elif counts["permanent"]:
        print("overall=permanent")
    else:
        print("overall=recoverable")

    return 0
