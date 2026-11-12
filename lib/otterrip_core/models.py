#!/usr/bin/env python3
"""Shared data models for OtterRip Core."""
from __future__ import annotations

import dataclasses
import enum
from typing import Optional


class SyncLevel(enum.IntEnum):
    """Hierarchy of lyric synchronization detail."""
    NONE = 0
    LINES = 1
    WORDS = 2
    SYLLABLES = 3


@dataclasses.dataclass
class LyricsResult:
    """Normalised lyric result from any provider."""
    provider: str
    title: str = ""
    artist: str = ""
    album: str = ""
    duration: Optional[float] = None
    language: str = ""
    sync_level: SyncLevel = SyncLevel.NONE
    plain_text: str = ""
    # List of (timestamp_seconds, text) — text may be a line, word, or syllable
    timed_entries: list[tuple[float, str]] = dataclasses.field(default_factory=list)
    original_format: str = ""          # lrc, ttml, qrc, json, txt, ...
    confidence: float = 0.0           # 0.0 – 1.0
    isrc: str = ""
    video_id: str = ""
    track_number: Optional[int] = None
    attribution: str = ""
    storable: bool = True             # may the result be cached?

    @property
    def has_sync(self) -> bool:
        return self.sync_level >= SyncLevel.LINES

    @property
    def is_plain(self) -> bool:
        return self.sync_level == SyncLevel.NONE

    def score(self, weight_sync: float = 0.4, weight_conf: float = 0.6) -> float:
        """Return a simple quality score in [0, 1]."""
        sync_ratio = self.sync_level / SyncLevel.SYLLABLES if self.sync_level else 0
        return sync_ratio * weight_sync + self.confidence * weight_conf
