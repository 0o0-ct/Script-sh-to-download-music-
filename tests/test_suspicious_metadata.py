#!/usr/bin/env python3
"""Tests for suspicious metadata detection and filename fallback.

Covers: is_suspicious_artist, parse_filename_for_metadata,
build_query_terms with suspicious artist fallback.
"""
import pytest
from otterrip_core.query_clean import (
    is_suspicious_artist,
    parse_filename_for_metadata,
    build_query_terms,
)


class TestIsSuspiciousArtist:
    """Test suspicious artist pattern detection."""

    def test_normal_artist(self):
        assert not is_suspicious_artist("The Beatles")
        assert not is_suspicious_artist("Ella Langley")
        assert not is_suspicious_artist("Morgan Wallen")

    def test_empty_artist(self):
        assert not is_suspicious_artist("")
        assert not is_suspicious_artist(None)

    def test_hits_pattern(self):
        assert is_suspicious_artist("60's 70's 80's 90's Hits")
        assert is_suspicious_artist("80s Hits")
        assert is_suspicious_artist("Best Hits Collection")

    def test_playlist_pattern(self):
        assert is_suspicious_artist("My Playlist")
        assert is_suspicious_artist("Chill Playlist")

    def test_compilation_pattern(self):
        assert is_suspicious_artist("Compilation Album")
        assert is_suspicious_artist("Summer Compilation 2024")

    def test_various_artists(self):
        assert is_suspicious_artist("Various Artists")
        assert is_suspicious_artist("Various Artists - Greatest Hits")

    def test_decade_pattern(self):
        assert is_suspicious_artist("70s 80s")
        assert is_suspicious_artist("Nightmares on Wax - 70s 80s")
        assert is_suspicious_artist("80's 90's")

    def test_best_of_pattern(self):
        assert is_suspicious_artist("Best of The Beatles")
        assert is_suspicious_artist("Best Of 2024")

    def test_top_pattern(self):
        assert is_suspicious_artist("Top 100 Songs")
        assert is_suspicious_artist("Top 50 Country")

    def test_channel_pattern(self):
        assert is_suspicious_artist("Music Channel")
        assert is_suspicious_artist("Vevo")

    def test_topic_pattern(self):
        assert is_suspicious_artist("Artist - Topic")

    def test_case_insensitive(self):
        assert is_suspicious_artist("HITS")
        assert is_suspicious_artist("hits")
        assert is_suspicious_artist("Hits")


class TestParseFilenameForMetadata:
    """Test filename parsing for artist and title extraction."""

    def test_numbered_artist_title(self):
        title, artist = parse_filename_for_metadata("001 - Ella Langley - Choosin Texas.m4a")
        assert "choosin" in title.lower() or "texas" in title.lower()
        assert "ella" in artist.lower() and "langley" in artist.lower()

    def test_artist_title_no_number(self):
        title, artist = parse_filename_for_metadata("The Beatles - Let It Be.mp3")
        assert "let it be" in title.lower()
        assert "beatles" in artist.lower()

    def test_numbered_title_only(self):
        title, artist = parse_filename_for_metadata("001 - Beechwood 4-5789.m4a")
        assert "beechwood" in title.lower()
        assert artist == ""

    def test_title_only(self):
        title, artist = parse_filename_for_metadata("Under the Bridge.m4a")
        assert "under" in title.lower() and "bridge" in title.lower()
        assert artist == ""

    def test_complex_filename(self):
        title, artist = parse_filename_for_metadata(
            "007 - Ella Langley & Morgan Wallen - I Can't Love You Anymore.m4a"
        )
        assert "love" in title.lower() or "anymore" in title.lower()
        assert "ella" in artist.lower() or "morgan" in artist.lower()

    def test_flac_extension(self):
        title, artist = parse_filename_for_metadata("01 - Artist - Title.flac")
        assert title
        assert artist

    def test_unicode_chars(self):
        title, artist = parse_filename_for_metadata("01 - Artista - Canción Española.mp3")
        assert "canción" in title.lower() or "española" in title.lower()
        assert "artista" in artist.lower()


class TestBuildQueryTermsWithSuspiciousArtist:
    """Test build_query_terms fallback when artist is suspicious."""

    def test_suspicious_artist_with_filename(self):
        """When artist is suspicious and filename is provided, use filename."""
        terms = build_query_terms(
            title="Beechwood 4-5789",
            artist="60's 70's 80's 90's Hits",
            album="",
            filename="001 - Beechwood 4-5789.m4a",
        )
        # The filename has no artist part, so artist should be empty
        # but title should come from filename
        assert terms["title"]

    def test_suspicious_artist_with_numbered_filename(self):
        """Numbered filename with artist should extract correctly."""
        terms = build_query_terms(
            title="Something Got Me Started",
            artist="Phillip Boa and the Voodooclub - 60's 70's 80's 90's 10",
            album="",
            filename="004 - Something Got Me Started.m4a",
        )
        # Filename has no artist, so it falls back to title only
        assert terms["title"]

    def test_normal_artist_ignores_filename(self):
        """Normal artist should not be replaced by filename."""
        terms = build_query_terms(
            title="Blinding Lights",
            artist="The Weeknd",
            album="After Hours",
            filename="001 - Random Artist - Random Song.m4a",
        )
        assert "weeknd" in terms["artist"].lower()
        assert "blinding" in terms["title"].lower()

    def test_suspicious_artist_no_filename(self):
        """Without filename, suspicious artist is kept as-is."""
        terms = build_query_terms(
            title="Some Song",
            artist="60's 70's 80's 90's Hits",
            album="",
            filename="",
        )
        # No filename to fall back to, so suspicious artist is used
        assert terms["artist"]

    def test_playlist_name_in_artist(self):
        """Artist containing 'playlist' triggers fallback."""
        terms = build_query_terms(
            title="My Song",
            artist="Chill Playlist",
            album="",
            filename="01 - Real Artist - Real Song.m4a",
        )
        assert "real" in terms["artist"].lower()

    def test_various_artists_fallback(self):
        """'Various Artists' triggers filename fallback."""
        terms = build_query_terms(
            title="Unknown",
            artist="Various Artists",
            album="Compilation",
            filename="05 - The Band - The Song.mp3",
        )
        assert "band" in terms["artist"].lower()

    def test_70s_80s_pattern(self):
        """'70s 80s' pattern triggers fallback."""
        terms = build_query_terms(
            title="Pumping on Your Stereo",
            artist="Nightmares on Wax - 70s 80s",
            album="",
            filename="036 - Pumping on Your Stereo.m4a",
        )
        # Filename has no artist part
        assert terms["title"]

    def test_vevo_channel(self):
        """'Vevo' in artist triggers fallback."""
        terms = build_query_terms(
            title="Song",
            artist="Artist Vevo",
            album="",
            filename="Artist Name - Song Title.mp3",
        )
        assert "name" in terms["artist"].lower()
