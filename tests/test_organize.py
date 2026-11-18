#!/usr/bin/env python3
"""Tests for organize.py."""
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from otterrip_core.organize import (
    Organizer,
    _sanitize_path_component,
    _format_track_number,
    _read_tags,
    run_organize,
    DEFAULT_TEMPLATE,
    AUDIO_EXTS,
)


class TestSanitizePathComponent:
    def test_remove_dangerous_chars(self):
        assert _sanitize_path_component('file<name>.txt') == 'filename.txt'

    def test_replace_multiple_spaces(self):
        assert _sanitize_path_component('hello   world') == 'hello world'

    def test_strip_whitespace(self):
        assert _sanitize_path_component('  hello  ') == 'hello'

    def test_empty_returns_unknown(self):
        assert _sanitize_path_component('') == 'Unknown'

    def test_normal_string(self):
        assert _sanitize_path_component('Normal Artist') == 'Normal Artist'


class TestFormatTrackNumber:
    def test_simple_number(self):
        assert _format_track_number("3") == "03"

    def test_with_total(self):
        assert _format_track_number("3/12") == "03"

    def test_single_digit(self):
        assert _format_track_number("1") == "01"

    def test_double_digit(self):
        assert _format_track_number("10") == "10"

    def test_invalid(self):
        assert _format_track_number("abc") == "00"

    def test_empty(self):
        assert _format_track_number("") == "00"


class TestReadTags:
    def test_nonexistent_file(self):
        tags = _read_tags(Path("/nonexistent/file.mp3"))
        assert tags["file"] == "/nonexistent/file.mp3"
        assert tags["name"] == "file.mp3"
        assert tags["stem"] == "file"
        assert tags["ext"] == ".mp3"


class TestOrganizer:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()

    def test_default_template(self):
        org = Organizer()
        assert org.template == DEFAULT_TEMPLATE

    def test_custom_template(self):
        org = Organizer("{artist}/{title}")
        assert org.template == "{artist}/{title}"

    def test_resolve_template_nonexistent_file(self):
        org = Organizer()
        result = org.resolve_template("/nonexistent/file.mp3")
        # Should return a fallback path
        assert "Unknown" in result or ".mp3" in result

    def test_organize_nonexistent_directory(self):
        org = Organizer()
        result = org.organize("/nonexistent/path", dry_run=True)
        assert result == {"moved": [], "skipped": [], "errors": []}

    def test_organize_empty_directory(self):
        org = Organizer()
        result = org.organize(self.tmpdir, dry_run=True)
        assert result["moved"] == []
        assert result["skipped"] == []
        assert result["errors"] == []

    def test_organize_dry_run(self):
        org = Organizer()
        result = org.organize(self.tmpdir, dry_run=True)
        # All items should have dry_run=True
        for item in result["moved"]:
            assert item["dry_run"] is True


class TestRunOrganize:
    def test_no_path(self):
        args = MagicMock()
        args.path = ""
        result = run_organize(args)
        assert result == 1

    def test_empty_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            args = MagicMock()
            args.path = tmpdir
            args.template = DEFAULT_TEMPLATE
            args.dry_run = True
            args.json = False
            result = run_organize(args)
            assert result == 0

    def test_apply_flag(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            args = MagicMock()
            args.path = tmpdir
            args.template = DEFAULT_TEMPLATE
            args.dry_run = True
            args.apply = True
            args.json = False
            result = run_organize(args)
            assert result == 0
