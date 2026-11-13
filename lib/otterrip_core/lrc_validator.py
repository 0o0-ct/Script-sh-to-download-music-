#!/usr/bin/env python3
"""LRC validation & normalisation — pure, testable module.

Accepts [mm:ss], [mm:ss.x], [mm:ss.xx] and [mm:ss.xxx].
Expands multi-timestamp lines, sorts, enforces monotonicity, strips
advertising / credit lines, preserves [ar:]/[ti:] headers, and
normalises every timestamp to [mm:ss.xx].
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


_HEADER_RE = re.compile(
    r"^\[(?:ti|ar|al|au|by|offset|length|re|ve)\:[^\]]*\]\s*$",
    re.IGNORECASE,
)
_TIMED_RE = re.compile(
    r"^\[(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?\]\s*(.*)$"
)
_ALL_TAGS_RE = re.compile(
    r"\[(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?\]"
)

_AD_PATTERNS = (
    r"letra\s*y\s*música",
    r"lyrics?\s*by",
    r"©\s*\d{4}",
    r"all rights reserved",
    r"subscríbete",
    r"subscribe",
    r"publicidad",
    r"advertisement",
    r"descarga.*(?:música|mp3)",
    r"www\.",
    r"http://",
    r"https://",
)
_AD_RE = re.compile("|".join(_AD_PATTERNS), re.IGNORECASE)

_MIN_TIMESTAMP_RATIO = 0.60
_LAST_TS_TOLERANCE_S = 3.0


@dataclass
class LrcValidationResult:
    """Outcome of :func:`validate_and_normalise_lrc`."""

    valid: bool
    entries: list[tuple[int, str]]  # (ms, text), sorted, monotonic
    headers: dict[str, str]         # [ti:], [ar:] etc.
    plain: str                      # timestamp-free text
    reason: str = ""                # human-readable failure reason
    warnings: list[str] = field(default_factory=list)


def _ms_from_tags(mins: int, secs: int, ms_part: str | None) -> int:
    total = mins * 60_000 + secs * 1000
    if ms_part:
        ms_part = (ms_part + "000")[:3]
        total += int(ms_part)
    return total


def _format_ts(ms: int) -> str:
    m, s = divmod(ms // 1000, 60)
    ms_part = ms % 1000
    return f"[{m:02d}:{s:02d}.{ms_part // 10:02d}]"


def _looks_like_ad(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if len(stripped) <= 2 and not stripped.isalnum():
        return True
    return bool(_AD_RE.search(stripped))


def parse_lrc(text: str) -> list[tuple[int, str]]:
    entries: list[tuple[int, str]] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or _HEADER_RE.match(line):
            continue
        tags = _ALL_TAGS_RE.findall(line)
        if not tags:
            continue
        text_part = _ALL_TAGS_RE.sub("", line, count=len(tags)).strip()
        for mins_s, secs_s, ms_s in tags:
            ms = _ms_from_tags(int(mins_s), int(secs_s), ms_s)
            entries.append((ms, text_part))
    return entries


def split_headers(text: str) -> tuple[dict[str, str], str]:
    headers: dict[str, str] = {}
    body_lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        m = re.match(r"^\[(\w+)\:\s*(.*)\]\s*$", line, re.IGNORECASE)
        if m:
            headers[m.group(1).lower()] = m.group(2).strip()
        else:
            body_lines.append(raw_line)
    return headers, "\n".join(body_lines)


def validate_and_normalise_lrc(
    lrc_text: str,
    audio_duration_s: float | None = None,
) -> LrcValidationResult:
    warnings: list[str] = []
    headers, body = split_headers(lrc_text)

    entries = parse_lrc(body)

    non_empty_lines = [
        ln for ln in body.splitlines() if ln.strip()
    ]
    if non_empty_lines:
        ratio = len(entries) / len(non_empty_lines)
        if ratio < _MIN_TIMESTAMP_RATIO:
            return LrcValidationResult(
                valid=False,
                entries=[],
                headers=headers,
                plain=body.strip(),
                reason=(
                    f"solo {ratio:.0%} de líneas con timestamp "
                    f"(mínimo {_MIN_TIMESTAMP_RATIO:.0%})"
                ),
                warnings=warnings,
            )

    filtered: list[tuple[int, str]] = []
    for ms, txt in entries:
        if _looks_like_ad(txt):
            warnings.append(f"línea publicitaria eliminada: {txt[:40]}...")
            continue
        if txt.strip():
            filtered.append((ms, txt))

    if not filtered:
        return LrcValidationResult(
            valid=False,
            entries=[],
            headers=headers,
            plain=body.strip(),
            reason="no hay líneas de letra válidas tras filtrar",
            warnings=warnings,
        )

    filtered.sort(key=lambda x: x[0])

    monotonic: list[tuple[int, str]] = []
    last_ms = -1
    for ms, txt in filtered:
        if ms < last_ms:
            warnings.append(
                f"timestamp no monotónico corregido: {ms}ms < {last_ms}ms"
            )
            ms = last_ms
        monotonic.append((ms, txt))
        last_ms = ms

    if audio_duration_s is not None:
        last_ts_s = monotonic[-1][0] / 1000.0
        if last_ts_s > audio_duration_s + _LAST_TS_TOLERANCE_S:
            return LrcValidationResult(
                valid=False,
                entries=monotonic,
                headers=headers,
                plain="\n".join(t for _, t in monotonic),
                reason=(
                    f"último timestamp ({last_ts_s:.1f}s) excede "
                    f"duración de audio ({audio_duration_s:.1f}s) + "
                    f"{_LAST_TS_TOLERANCE_S}s"
                ),
                warnings=warnings,
            )

    plain = "\n".join(txt for _, txt in monotonic)

    return LrcValidationResult(
        valid=True,
        entries=monotonic,
        headers=headers,
        plain=plain,
        warnings=warnings,
    )


def normalise_lrc(entries: list[tuple[int, str]]) -> str:
    lines = [
        _format_ts(ms) + (" " + txt if txt else "")
        for ms, txt in entries
    ]
    return "\n".join(lines)


def shift_lrc(lrc_text: str, ms_offset: int) -> str:
    entries = parse_lrc(lrc_text)
    shifted = [(max(0, ms + ms_offset), txt) for ms, txt in entries]
    shifted.sort(key=lambda x: x[0])
    return normalise_lrc(shifted)