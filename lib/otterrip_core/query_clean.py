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
    r"\(official\s+music\s*video\)",
    r"\[official\s+music\s*video\]",
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
    r"\(remaster(?:ed)?\s*(?:2\d{3})?\)",
    r"\(4k\s+remaster(?:ed)?\s*(?:2\d{3})?\)",
    r"\[4k\s+remaster(?:ed)?\s*(?:2\d{3})?\]",
    r"\(remaster\)",
    r"\[remaster\]",
    r"\b4k\s+remaster\b",
    r"\[remaster(?:ed)?\s*(?:2\d{3})?\]",
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
    # YouTube video ID in brackets/parens at end of title, e.g. [NdYWuo9OFAw]
    r"\s*\[[a-zA-Z0-9_-]{11}\s*\]",
    r"\s*\([a-zA-Z0-9_-]{11}\s*\)",
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


def _norm_eq(a: str, b: str) -> bool:
    """Accent- and case-insensitive equality ("Pablo Martínez" == "Pablo Martinez")."""
    return normalize_unicode(a) == normalize_unicode(b)


# Album tags that carry no information — sent as a filter they poison the
# exact-match lookup ("Descalzos" exists, but album_name=NA returns 404).
_GARBAGE_ALBUMS = frozenset(
    {
        "",
        "na",
        "n/a",
        "none",
        "null",
        "unknown",
        "unknown album",
        "album desconocido",
        "desconocido",
        "single",
        "untitled",
    }
)


def _clean_album(album: str) -> str:
    """Lowercase-agnostic drop of placeholder album tags ("NA", "Unknown", …)."""
    if not album:
        return ""
    cleaned = _WS_RE.sub(" ", album).strip(" -–—|").strip()
    if normalize_unicode(cleaned) in _GARBAGE_ALBUMS:
        return ""
    return cleaned


def _strip_repeated_artist_prefix(title: str) -> tuple[str, str | None]:
    """Remove repeated artist prefix from YouTube Music titles.

    YouTube Music sometimes embeds the artist name twice at the start of the
    title, e.g. "JON CARLO - Jon Carlo - Quema Mi Vida".  This function detects
    that pattern (first two dash-separated segments match) and strips the
    duplicate prefix so only the actual track name remains.

    Returns ``(cleaned_title, extracted_artist)``.  The extracted artist is
    useful when the metadata artist field is empty.

    Examples:
        "JON CARLO - Jon Carlo - Quema Mi Vida" → ("Quema Mi Vida", "Jon Carlo")
        "ARTISTA - Artista - Canción" → ("Canción", "Artista")
        "Riptide" → ("Riptide", None)
    """
    # Normalise en/em dashes to hyphen before splitting
    norm = title.replace("–", "-").replace("—", "-")
    parts = norm.split(" - ", 2)
    if len(parts) >= 3:
        part1 = parts[0].strip()
        part2 = parts[1].strip()
        if part1 and part2 and _norm_eq(part1, part2):
            return parts[2].strip(), part2
    return title, None


def strip_artist_from_title(title: str, artist: str) -> str:
    """Remove the artist name from a compound YouTube title.

    Handles the patterns exact-match lyrics APIs cannot tolerate:
        "Artist - Song"             → "Song"   (prefix)
        "Song - Artist"             → "Song"   (suffix)
        "Song - Artist - Playlist"  → "Song"   (suffix + uploader context)
    Comparisons are accent- and case-insensitive, so "Pablo Martínez"
    still matches the tag "Pablo Martinez".

    A segment equal to the artist also discards everything after it: in
    "Un Paso A La Vez - Aldana Canale - Oración para momentos difíciles"
    the trailing part is the playlist name, not part of the song title.
    """
    if not artist or not title:
        return title
    norm = title.replace("–", "-").replace("—", "-")
    parts = [p.strip() for p in norm.split(" - ")]
    if len(parts) < 2:
        return title
    # Leading duplicated artist: "Artist - Song" / "Artist - Artist - Song".
    start = 0
    if _norm_eq(parts[0], artist):
        start = 1
        if len(parts) >= 2 and _norm_eq(parts[1], artist):
            start = 2
    # Trailing/mid artist: drop that segment and everything after it.
    end = len(parts)
    for i in range(len(parts) - 1, start, -1):
        if _norm_eq(parts[i], artist):
            end = i
            break
    cleaned = " - ".join(parts[start:end]).strip()
    return cleaned or title


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

    # Remove the artist from compound titles — "Artist - Song",
    # "Song - Artist", "Song - Artist - Playlist". Accent/case-insensitive:
    # including the artist again in the title makes the query overly
    # specific and exact-match providers fail to find the track.
    if clean_a and clean_t:
        stripped = strip_artist_from_title(clean_t, clean_a)
        if stripped:
            clean_t = stripped
        # Also handle "Artist: Song" form (length-guarded: NFKD can change
        # the string length, in which case we skip the slice).
        n_t = normalize_unicode(clean_t)
        n_prefix = normalize_unicode(clean_a + ":")
        if (
            n_t.startswith(n_prefix)
            and len(n_t) == len(clean_t)
            and len(n_prefix) == len(clean_a) + 1
        ):
            clean_t = clean_t[len(clean_a) + 1:].strip()

    # Move feat. artists from title into artist field for better matching
    if feat_artist:
        if clean_a:
            clean_a = f"{clean_a} {feat_artist}"
        else:
            clean_a = feat_artist

    return {
        "title": clean_t,
        "artist": clean_a,
        "album": _clean_album(album),
        "search": f"{clean_t} {clean_a}".strip(),
    }