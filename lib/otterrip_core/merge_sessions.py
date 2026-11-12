#!/usr/bin/env python3
"""Merge session files (merge-sessions command)."""
from __future__ import annotations

import glob
from pathlib import Path


def run_merge_sessions(args) -> int:
    """Merge yt-dlp session files, deduplicating by video ID."""
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    seen: set[str] = set()
    lines: list[str] = []

    for pattern in args.inputs:
        for filepath in sorted(glob.glob(pattern)):
            p = Path(filepath)
            if not p.is_file():
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line not in seen:
                    seen.add(line)
                    lines.append(line)

    output.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    return 0
