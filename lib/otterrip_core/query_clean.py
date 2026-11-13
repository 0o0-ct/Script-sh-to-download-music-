#!/usr/bin/env python3
"""Query cleaning — pure, testable module for normalising search terms.

Strips common YouTube / streaming noise from titles and artists so that
provider lookups (LRCLIB, Multi-Web) return better matches.
"""
from __future__ import annotations

import re
import unicodedata

# Tokens commonly appended to YouTube titles — removed case-insensitively.
_TITLE_NOISE = (
    r"\(official\s*(?:video|audio|lyric|visualizer|visualiser|video\s*lyrics?)\)",
    r"\[official\s*(?:video|audio|lyric|visualizer|visualiser)\]",
    r"\(lyrics?\)",
    r"\[lyrics?\]",
    r"\(lyric\s*video\)",
    r"\[lyric\s*video\]",
    r"\(video\)",
    r"\[video\]",
    r"\(hd\)",
    r"\[hd\]",
    r"\(4k\)",
    r"\[4k\]",
    r"\(remastered?\s*(?:2\d{3})?\)",
    r"\[remastered?\s*(?:2\d{3})?\]",
    r"\(live.*?\)",
    r"\[live.*?\]",
    r"\(acoustic.*?\)",
    r"\[acoustic.*?\]",
    r"\(feat\..*?\)",
    r"\[feat\..*?\]",
    r"\(ft\..*?\)",
    r"\[ft\..*?\]",
    r"\(featuring.*?\)",
    r"\[featuring.*?\]",
    r"official\s*(?:video|audio|lyric)",
    r"lyrics?\s*video",
    r"\bhd\b",
    r"\b4k\b",
)

_TITLE_NOISE_RE = re.compile("|".join(_TITLE_NOISE), re.IGNORECASE)

# Trailing " - Topic" / "Topic" suffixes on YouTube Music artist channels.
_ARTIST_TOPIC_RE = re.compile(r"\s*-\s*Topic\s*$", re.IGNORECASE)

# Multiple whitespace.
_WS_RE = re.compile(r"\s+")

# Parenthesised feat./ft. pattern — does NOT require end-of-string,
# because titles often have trailing noise like [Lyrics] after (feat. ...).
_FEAT_PAREN_RE = re.compile(r"\s*\(\s*(?:feat\.?|ft\.?|featuring)\s+(.+?)\s*\)", re.IGNORECASE)


def normalize_unicode(value: str) -> str:
    """NFKD-normalise, strip combining marks, lowercase."""
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    stripped = "".join(
        ch for ch in decomposed if not unicodedata.combining(ch)
    )
    return stripped.casefold().strip()


def clean_title(title: str) -> str:
    """Remove YouTube noise from a track title.

    Examples:
        "Bohemian Rhapsody (Official Video)" → "Bohemian Rhapsody"
        "Shape of You [Lyrics]" → "Shape of You"
        "Blinding Lights (feat. Kendrick Lamar)" → "Blinding Lights"
    """
    if not title:
        return title
    cleaned = _TITLE_NOISE_RE.sub("", title)
    cleaned = cleaned.replace("–", "-").replace("—", "-")
    # Normalise dash separators but keep meaningful ones inside title
    cleaned = _WS_RE.sub(" ", cleaned).strip(" -–—|")
    return cleaned.strip()


def clean_artist(artist: str) -> str:
    """Remove YouTube Music " - Topic" channel suffix and noise."""
    if not artist:
        return artist
    cleaned = _ARTIST_TOPIC_RE.sub("", artist)
    cleaned = _WS_RE.sub(" ", cleaned).strip(" -–—|")
    return cleaned.strip()


def split_feat(title: str) -> tuple[str, str | None]:
    """Split ``feat.`` / ``ft.`` artists out of a title.

    Handles ``(feat. ...)`` and ``(ft. ...)`` parenthetical forms.
    Returns ``(clean_title, feat_artist)``.
    """
    if not title:
        return title, None
    # Try parenthetical form first — matches anywhere in the string.
    m = _FEAT_PAREN_RE.search(title)
    if m:
        return title[: m.start()].strip(), m.group(1).strip()
    # Fallback to standard form (no parentheses).
    m = re.search(
        r"\s+(?:feat\.?|ft\.?|featuring)\s+(.+?)\s*$", title, re.IGNORECASE
    )
    if m:
        return title[: m.start()].strip(), m.group(1).strip()
    return title, None


def build_query_terms(
    title: str,
    artist: str,
    album: str = "",
    feat_artist: str | None = None,
) -> dict[str, str]:
    """Build cleaned query terms for provider lookups.

    Returns a dict with keys ``title``, ``artist``, ``album`` and
    ``search`` (combined title + artist for fallback providers).
    """
    clean_t = clean_title(title)
    clean_a = clean_artist(artist)

    # Move feat. artists from title into artist field for better matching
    if feat_artist:
        if clean_a:
            clean_a = f"{clean_a} {feat_artist}"
        else:
            clean_a = feat_artist

    return {
        "title": clean_t,
        "artist": clean_a,
        "album": clean_title(album) if album else "",
        "search": f"{clean_t} {clean_a}".strip(),
    }