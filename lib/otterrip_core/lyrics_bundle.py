#!/usr/bin/env python3
"""LyricsBundle — shared result object for the lyrics pipeline.

Defines the contract between provider adapters, the cascade selector,
and the tag/file writers (Part 2).  All fields are populated by the
caller; helpers are provided for common transformations.
"""
from __future__ import annotations

import dataclasses
from typing import Optional


@dataclasses.dataclass
class LyricsBundle:
    """Normalised lyric result ready for embedding or side-car file.

    Attributes:
        kind: "word" | "line" | "plain" | "instrumental" | "none"
        lrc: Normalised LRC string with ``[mm:ss.xx]`` timestamps
             (Enhanced LRC if kind == "word").  ``None`` when kind is
             "plain", "instrumental" or "none".
        plain: Timestamp-free lyrics text, or ``None`` when kind is
               "instrumental" or "none".
        lines: List of ``(milliseconds, text)`` tuples derived from
               ``lrc``, guaranteed non-decreasing in time.
        source: Provider name plus origin identifier / URL.
        audio_duration_ms: Duration (ms) of the *final* audio file
                           (ffprobe / mutagen), used for validation.
        ref_duration_ms: Duration (ms) reported by the provider source
                         (e.g. LRCLIB track duration), used for
                         |Δduration| checks.
        offset_ms: Millisecond correction already applied to ``lines``
                   and ``lrc`` timestamps.
        confidence: Quality score 0.0 – 1.0.
        warnings: Human-readable warnings (duration mismatch, etc.).
    """

    kind: str
    lrc: Optional[str] = None
    plain: Optional[str] = None
    lines: list[tuple[int, str]] = dataclasses.field(default_factory=list)
    source: str = ""
    title: str = ""
    artist: str = ""
    album: str = ""
    audio_duration_ms: Optional[int] = None
    ref_duration_ms: Optional[int] = None
    offset_ms: int = 0
    confidence: float = 0.0
    warnings: list[str] = dataclasses.field(default_factory=list)
    headers: dict[str, str] = dataclasses.field(default_factory=dict)

    # ------------------------------------------------------------------
    # Derived helpers
    # ------------------------------------------------------------------

    @property
    def has_sync(self) -> bool:
        return self.kind in ("line", "word") and bool(self.lines)

    @property
    def is_plain(self) -> bool:
        return self.kind == "plain"

    @property
    def is_instrumental(self) -> bool:
        return self.kind == "instrumental"

    @property
    def is_none(self) -> bool:
        return self.kind == "none"

    def duration_delta_ms(self) -> Optional[int]:
        """|audio_duration_ms − ref_duration_ms| in ms, or None."""
        if self.audio_duration_ms is None or self.ref_duration_ms is None:
            return None
        return abs(self.audio_duration_ms - self.ref_duration_ms)

    # ── Backward-compatible aliases for legacy LyricsResult interface ─

    @property
    def provider(self) -> str:
        """First token of source (e.g. \"lrclib\")."""
        return self.source.split(" | ")[0] if self.source else ""



    @property
    def duration(self) -> float | None:
        return (
            self.audio_duration_ms / 1000.0
            if self.audio_duration_ms is not None
            else None
        )

    @property
    def sync_level(self) -> int:
        return {"word": 2, "line": 1, "plain": 0, "instrumental": 0, "none": 0}.get(
            self.kind, 0
        )

    @property
    def plain_text(self) -> str:
        return self.plain or ""

    @property
    def timed_entries(self) -> list[tuple[float, str]]:
        return [(ms / 1000.0, txt) for ms, txt in self.lines]

    @property
    def original_format(self) -> str:
        return "lrc" if self.has_sync else "txt"

    @property
    def isrc(self) -> str:
        return ""

    @property
    def video_id(self) -> str:
        return ""

    @property
    def track_number(self) -> int | None:
        return None

    @property
    def attribution(self) -> str:
        return ""

    @property
    def storable(self) -> bool:
        return True

    @property
    def language(self) -> str:
        return self.headers.get("lang", "") if hasattr(self, "headers") else ""

    def to_legacy_result(self, provider: str) -> dict:
        """Emit a plain dict compatible with legacy ``LyricsResult`` fields."""
        return {
            "provider": provider,
            "title": "",
            "artist": "",
            "album": "",
            "duration": (
                self.audio_duration_ms / 1000.0
                if self.audio_duration_ms is not None
                else None
            ),
            "sync_level": (
                2 if self.kind == "word" else 1 if self.kind == "line" else 0
            ),
            "plain_text": self.plain or "",
            "timed_entries": [
                (ms / 1000.0, txt) for ms, txt in self.lines
            ],
            "confidence": self.confidence,
            "warnings": list(self.warnings),
        }


def bundle_from_legacy(
    provider: str,
    title: str,
    artist: str,
    album: str,
    duration_s: Optional[float],
    sync_level: int,
    plain_text: str,
    timed_entries: list[tuple[float, str]],
    confidence: float,
    source: str = "",
    ref_duration_ms: Optional[int] = None,
    warnings: Optional[list[str]] = None,
) -> LyricsBundle:
    """Build a :class:`LyricsBundle` from legacy provider fields.

    This is a transitional helper while adapters are migrated to produce
    :class:`LyricsBundle` directly.
    """
    kind_map = {
        2: "word",
        1: "line",
        0: "plain",
    }
    kind = kind_map.get(sync_level, "plain")

    lines_ms: list[tuple[int, str]] = [
        (int(round(ts * 1000)), txt) for ts, txt in timed_entries
    ]
    lines_ms.sort(key=lambda x: x[0])

    lrc: Optional[str] = None
    if kind in ("line", "word") and lines_ms:
        lrc_lines = [
            f"[{ms // 60000:02d}:{(ms % 60000) // 1000:02d}.{(ms % 1000) // 10:02d}] {txt}"
            for ms, txt in lines_ms
        ]
        lrc = "\n".join(lrc_lines)

    headers: dict[str, str] = {}
    if title:
        headers["ti"] = title
    if artist:
        headers["ar"] = artist
    if album:
        headers["al"] = album

    return LyricsBundle(
        kind=kind,
        lrc=lrc,
        plain=plain_text or None,
        lines=lines_ms,
        source=source,
        title=title,
        artist=artist,
        album=album,
        audio_duration_ms=(
            int(round(duration_s * 1000)) if duration_s is not None else None
        ),
        ref_duration_ms=ref_duration_ms,
        confidence=max(0.0, min(1.0, confidence)),
        warnings=warnings or [],
    )