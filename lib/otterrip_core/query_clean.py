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
    r"\(en\s+vivo.*?\)",
    r"\[en\s+vivo.*?\]",
    r"\(acoustic.*?\)",
    r"\[acoustic.*?\]",
    r"\(ac[úu]stico.*?\)",
    r"\[ac[úu]stico.*?\]",
    r"\(feat\..*?\)",
    r"\[feat\..*?\]",
    r"\(ft\..*?\)",
    r"\[ft\..*?\]",
    r"\(featuring.*?\)",
    r"\[featuring.*?\]",
    # YouTube Music appends artist after │ (fullwidth) or | (pipe) separator in title
    r"\s*[│\uFF5C|]\s*.*$",
    # Spanish: (Letra) standalone
    r"\(letra\)",
    # (En Vivo desde ...)
    r"\(en\s+vivo\s+desde\s+.*?\)",
    # (Versión ...)
    r"\(versi[óo]n\s+.*?\)",
    # (Audio Oficial) / (Official Audio)
    r"\(audio\s+oficial\)",
    r"\(official\s+audio\)",
    r"official\s*(?:video|audio|lyric)",
    r"lyrics?\s*video",
    r"\bhd\b",
    r"\b4k\b",
)

# Trailing genre/channel suffixes — applied separately so $ anchor works.
_TRAILING_SUFFIX_RE = re.compile(
    r"\s*-\s*(?:musica\s+catolica|catholic\s+music|music\s+catolica)\s*$",
    re.IGNORECASE,
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


def _strip_repeated_artist_prefix(title: str) -> tuple[str, str | None]:
    """Remove repeated artist prefix from YouTube Music titles.

    YouTube Music sometimes embeds the artist name twice at the start of the
    title, e.g. "JON CARLO - Jon Carlo - Quema Mi Vida".  This function detects
    that pattern (first two dash-separated segments match case-insensitively)
    and strips the duplicate prefix so only the actual track name remains.

    Returns ``(cleaned_title, extracted_artist)``.  The extracted artist is
    useful when the metadata artist field is empty.

    Examples:
        "JON CARLO - Jon Carlo - Quema Mi Vida" → ("Quema Mi Vida", "Jon Carlo")
        "ARTISTA - Artista - Canción" → ("Canción", "Artista")
        "Riptide" → ("Riptide", None)
    """
    parts = title.split(" - ", 2)
    if len(parts) >= 3:
        part1 = parts[0].strip()
        part2 = parts[1].strip()
        if part1 and part2 and part1.lower() == part2.lower():
            return parts[2].strip(), part2
    return title, None


def clean_title(title: str) -> str:
    """Remove YouTube noise from a track title.

    Examples:
        "Bohemian Rhapsody (Official Video)" → "Bohemian Rhapsody"
        "Shape of You [Lyrics]" → "Shape of You"
        "Blinding Lights (feat. Kendrick Lamar)" → "Blinding Lights"
        "JON CARLO - Jon Carlo - Quema Mi Vida" → "Quema Mi Vida"
        "Mi Gran Pasión (Audio Oficial) - Musica Catolica" → "Mi Gran Pasión"
    """
    if not title:
        return title
    # Strip repeated artist prefix first (before other noise removal)
    cleaned, _ = _strip_repeated_artist_prefix(title)
    cleaned = _TITLE_NOISE_RE.sub("", cleaned)
    # Remove trailing genre/channel suffixes like "- Musica Catolica"
    cleaned = _TRAILING_SUFFIX_RE.sub("", cleaned)
    cleaned = cleaned.replace("–", "-").replace("—", "-")
    # Normalise dash separators but keep meaningful ones inside title
    cleaned = _WS_RE.sub(" ", cleaned).strip(" -–—|")
    return cleaned.strip()


def clean_artist(artist: str) -> str:
    """Remove YouTube Music " - Topic" channel suffix and noise.

    When multiple artists are comma-separated (e.g. "Un Corazón, Kim Richards,
    Marco Barrientos"), keep only the first artist.  LRCLIB matching is much
    more accurate with a single primary artist name.
    """
    if not artist:
        return artist
    cleaned = _ARTIST_TOPIC_RE.sub("", artist)
    # Keep only the first artist when comma-separated.
    if "," in cleaned:
        cleaned = cleaned.split(",", 1)[0]
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

    When the title contains a repeated artist prefix (e.g. "JON CARLO - Jon Carlo -
    Quema Mi Vida") and the artist field is empty, the artist name is extracted
    from the title automatically.
    """
    # Strip repeated artist prefix and extract artist if available
    stripped_title, extracted_artist = _strip_repeated_artist_prefix(title)
    clean_t = clean_title(stripped_title)

    # Use extracted artist from title if the provided artist is empty
    if not artist and extracted_artist:
        clean_a = clean_artist(extracted_artist)
    else:
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