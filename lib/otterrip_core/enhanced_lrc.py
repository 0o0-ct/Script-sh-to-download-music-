#!/usr/bin/env python3
"""Enhanced LRC — word-level timestamp format.

Supports the Enhanced LRC syntax:

    [mm:ss.xxx] <mm:ss.xxx> word1 <mm:ss.xxx> word2 ...

The line-level timestamp precedes the text; each word carries its own
opening timestamp inside angle brackets.  A closing timestamp may also
be present: ``<mm:ss.xxx>word</mm:ss.xxx>``.

Pure module — no network, no heavy dependencies.  The optional
alignment engine (stable-ts / WhisperX) lives in a separate module
that imports this one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


# ── Data model ─────────────────────────────────────────────────────

@dataclass
class WordSpan:
    """One word with its time span."""
    text: str
    start_ms: int
    end_ms: int = 0


@dataclass
class EnhancedLine:
    """One Enhanced LRC line."""
    line_ms: int          # line-level timestamp
    words: list[WordSpan] = field(default_factory=list)
    raw_text: str = ""    # fallback plain text


@dataclass
class EnhancedLrc:
    """Parsed Enhanced LRC document."""
    lines: list[EnhancedLine] = field(default_factory=list)
    headers: dict[str, str] = field(default_factory=dict)
    plain_text: str = ""


# ── Regex patterns ─────────────────────────────────────────────────

# Line-level timestamp: [mm:ss.xx] or [mm:ss.xxx]
_LINE_TS_RE = re.compile(r"\[(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?\]")

# Word-level timestamp: <mm:ss.xxx>
_WORD_TS_RE = re.compile(r"<(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?>")

# Header tags: [ti:], [ar:], etc.
_HEADER_RE = re.compile(r"^\[(ti|ar|al|au|by|offset|length|re|ve)\:\s*(.*)\]\s*$", re.IGNORECASE)


def _ms_from_parts(mins: int, secs: int, ms_part: str | None) -> int:
    total = mins * 60_000 + secs * 1000
    if ms_part:
        ms_part = (ms_part + "000")[:3]
        total += int(ms_part)
    return total


def _format_ts_ms(ms: int) -> str:
    """Format milliseconds as [mm:ss.xx] (2-digit centiseconds for compatibility)."""
    m, remainder = divmod(ms, 60_000)
    s, ms_part = divmod(remainder, 1000)
    return f"[{m:02d}:{s:02d}.{ms_part // 10:02d}]"


def _format_word_ts_ms(ms: int) -> str:
    """Format milliseconds as <mm:ss.xx> (2-digit centiseconds for compatibility)."""
    m, remainder = divmod(ms, 60_000)
    s, ms_part = divmod(remainder, 1000)
    return f"<{m:02d}:{s:02d}.{ms_part // 10:02d}>"


# ── Parser ─────────────────────────────────────────────────────────

def parse_enhanced_lrc(text: str) -> EnhancedLrc:
    """Parse Enhanced LRC text into structured data.

    Handles:
    - Standard LRC lines: ``[mm:ss.xx] text``
    - Enhanced lines: ``[mm:ss.xx] <mm:ss.xxx> w1 <mm:ss.xxx> w2``
    - Headers: ``[ti:Title]``, ``[ar:Artist]``, etc.
    """
    elrc = EnhancedLrc()
    current_line: EnhancedLine | None = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue

        # Header?
        hm = _HEADER_RE.match(line)
        if hm:
            elrc.headers[hm.group(1).lower()] = hm.group(2).strip()
            continue

        # Find line-level timestamp
        line_match = _LINE_TS_RE.search(line)
        if not line_match:
            # Continuation of previous line or plain text
            if current_line is not None:
                current_line.raw_text += " " + line
            continue

        line_ms = _ms_from_parts(
            int(line_match.group(1)),
            int(line_match.group(2)),
            line_match.group(3),
        )

        # Text after line timestamp
        after_ts = line[line_match.end():].strip()

        # Check for word-level timestamps
        word_matches = list(_WORD_TS_RE.finditer(after_ts))

        if word_matches:
            # Enhanced line with word timestamps
            words: list[WordSpan] = []
            prev_end = 0
            text_parts: list[str] = []

            pos = 0
            for wm in word_matches:
                # Text before this word timestamp
                before = after_ts[pos:wm.start()].strip()
                if before:
                    text_parts.append(before)

                w_start = _ms_from_parts(
                    int(wm.group(1)),
                    int(wm.group(2)),
                    wm.group(3),
                )

                # Find the word text after the timestamp
                rest = after_ts[wm.end():]
                # Skip optional closing bracket content
                word_text = ""
                if rest:
                    # Word ends at next timestamp or end of string
                    next_ts = _WORD_TS_RE.search(rest)
                    if next_ts:
                        word_text = rest[:next_ts.start()].strip()
                    else:
                        word_text = rest.strip()

                # Clean angle brackets from word text
                word_text = re.sub(r"[<>]", "", word_text).strip()

                if word_text:
                    words.append(WordSpan(
                        text=word_text,
                        start_ms=w_start,
                        end_ms=0,  # will be computed
                    ))
                    text_parts.append(word_text)

                pos = wm.end()

            # Remaining text after last word timestamp
            if pos < len(after_ts):
                remaining = after_ts[pos:].strip()
                remaining = re.sub(r"[<>]", "", remaining).strip()
                if remaining:
                    text_parts.append(remaining)

            # Compute end_ms for each word (start of next word)
            for i, w in enumerate(words):
                if i + 1 < len(words):
                    w.end_ms = words[i + 1].start_ms
                # Last word's end_ms stays 0 (unknown)

            current_line = EnhancedLine(
                line_ms=line_ms,
                words=words,
                raw_text=" ".join(text_parts),
            )
            elrc.lines.append(current_line)
        else:
            # Standard LRC line (no word timestamps)
            plain = after_ts
            current_line = EnhancedLine(
                line_ms=line_ms,
                words=[],
                raw_text=plain,
            )
            elrc.lines.append(current_line)

    # Build plain text
    elrc.plain_text = "\n".join(el.raw_text for el in elrc.lines if el.raw_text)

    return elrc


# ── Serializer ─────────────────────────────────────────────────────

def serialize_enhanced_lrc(elrc: EnhancedLrc) -> str:
    """Serialize EnhancedLrc back to text format.

    Lines with word timestamps use Enhanced format.
    Lines without word timestamps use standard LRC format.
    """
    out_lines: list[str] = []

    # Headers
    for key in ("ti", "ar", "al", "au", "by", "ve"):
        if key in elrc.headers:
            out_lines.append(f"[{key}:{elrc.headers[key]}]")

    for el in elrc.lines:
        if el.words:
            # Enhanced format with word timestamps
            parts = [_format_ts_ms(el.line_ms)]
            for w in el.words:
                parts.append(f" {_format_word_ts_ms(w.start_ms)} {w.text}")
            out_lines.append("".join(parts))
        else:
            # Standard LRC format
            if el.raw_text:
                out_lines.append(f"{_format_ts_ms(el.line_ms)} {el.raw_text}")

    return "\n".join(out_lines)


# ── Conversion helpers ─────────────────────────────────────────────

def standard_lrc_to_enhanced(lrc_text: str) -> EnhancedLrc:
    """Convert standard LRC to EnhancedLrc (word timestamps = 0).

    This creates the skeleton; the alignment engine fills in word times.
    """
    elrc = EnhancedLrc()
    line_ts_re = re.compile(r"\[(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?\]\s*(.*)")

    for raw in lrc_text.splitlines():
        line = raw.strip()
        if not line:
            continue
        hm = _HEADER_RE.match(line)
        if hm:
            elrc.headers[hm.group(1).lower()] = hm.group(2).strip()
            continue

        m = line_ts_re.match(line)
        if m:
            line_ms = _ms_from_parts(
                int(m.group(1)), int(m.group(2)), m.group(3),
            )
            text = m.group(4).strip()
            # Split into words (no timestamps yet)
            words = [WordSpan(text=w, start_ms=line_ms, end_ms=0)
                     for w in text.split() if w]
            elrc.lines.append(EnhancedLine(
                line_ms=line_ms,
                words=words,
                raw_text=text,
            ))

    elrc.plain_text = "\n".join(el.raw_text for el in elrc.lines if el.raw_text)
    return elrc


def enhanced_to_standard_lrc(elrc: EnhancedLrc) -> str:
    """Convert EnhancedLrc to standard LRC (line-level only)."""
    lines: list[str] = []
    for key in ("ti", "ar", "al"):
        if key in elrc.headers:
            lines.append(f"[{key}:{elrc.headers[key]}]")
    for el in elrc.lines:
        if el.raw_text:
            lines.append(f"{_format_ts_ms(el.line_ms)} {el.raw_text}")
    return "\n".join(lines)


# ── Validation ─────────────────────────────────────────────────────

def validate_enhanced(elrc: EnhancedLrc, tolerance_ms: int = 500) -> list[str]:
    """Validate word timestamps against line timestamps.

    Returns a list of warnings.  Empty list = valid.
    """
    warnings: list[str] = []

    for i, el in enumerate(elrc.lines):
        if not el.words:
            continue

        # Check monotonicity within line
        last_ms = -1
        for j, w in enumerate(el.words):
            if w.start_ms < last_ms:
                warnings.append(
                    f"line {i+1}, word {j+1}: timestamp not monotonic "
                    f"({w.start_ms}ms < {last_ms}ms)"
                )
                w.start_ms = last_ms
            last_ms = w.start_ms

            # Check word is within line ± tolerance
            if abs(w.start_ms - el.line_ms) > tolerance_ms and j == 0:
                warnings.append(
                    f"line {i+1}: first word {w.start_ms}ms far from "
                    f"line {el.line_ms}ms (tolerance {tolerance_ms}ms)"
                )

        # Check line-level monotonicity
        if i > 0:
            prev = elrc.lines[i - 1]
            if el.line_ms < prev.line_ms:
                warnings.append(
                    f"line {i+1}: line timestamp {el.line_ms}ms < "
                    f"previous {prev.line_ms}ms"
                )

    return warnings


# ── Alignment stub ─────────────────────────────────────────────────

def align_words_stub(elrc: EnhancedLrc, audio_path: str) -> EnhancedLrc | None:
    """Attempt word-level alignment using stable-ts.

    Returns None if dependencies are not available.
    This is an opt-in feature; failure is graceful.
    """
    try:
        import stable_ts
    except ImportError:
        return None

    try:
        import librosa
    except ImportError:
        return None

    # Load audio
    try:
        audio, sr = librosa.load(audio_path, sr=16000)
    except Exception:
        return None

    # Align each line's words
    for el in elrc.lines:
        if not el.words or not el.raw_text:
            continue

        words_text = [w.text for w in el.words]

        try:
            aligner = stable_ts.AlignmentModel()
            result = aligner.align(words_text, audio, sr)

            for j, (start_s, end_s) in enumerate(result):
                if j < len(el.words):
                    el.words[j].start_ms = int(start_s * 1000)
                    el.words[j].end_ms = int(end_s * 1000)
        except Exception:
            # Graceful fallback: keep line-level timestamps
            pass

    return elrc
