#!/usr/bin/env python3
"""Base provider adapter interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from otterrip_core.models import LyricsResult, SyncLevel


@dataclass
class SongQuery:
    """Parameters for searching lyrics."""
    title: str
    artist: str
    album: str = ""
    duration: Optional[float] = None
    isrc: str = ""
    track_number: Optional[int] = None
    language: str = ""
    video_id: str = ""


class BaseProvider(ABC):
    """Abstract base class for lyric providers."""

    name: str = "base"
    enabled_by_default: bool = True
    requires_credentials: bool = False

    @abstractmethod
    async def search(self, query: SongQuery) -> list[LyricsResult]:
        """Search for lyrics. Returns list of results (may be empty)."""
        ...

    def is_available(self) -> bool:
        """Check if this provider can be used (dependencies, credentials, etc.)."""
        return True

    @classmethod
    def supported_formats(cls) -> list[str]:
        """Return list of output formats this provider supports."""
        return ["lrc", "txt"]

    @classmethod
    def max_sync_level(cls) -> SyncLevel:
        """Return the maximum sync level this provider can provide."""
        return SyncLevel.LINES
