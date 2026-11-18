#!/usr/bin/env python3
"""Tests for the yt-dlp manager module.

Tests cover:
- Version comparison
- Update interval checking
- State file management
- Error classification
- Mocked update/rollback operations
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Add lib to path
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))

from otterrip_core import ytdlp_manager


class TestVersionComparison:
    """Test version comparison logic."""

    def test_equal_versions(self):
        assert ytdlp_manager._compare_versions("2024.02.01", "2024.02.01") == 0

    def test_newer_version(self):
        assert ytdlp_manager._compare_versions("2024.03.01", "2024.02.01") == 1

    def test_older_version(self):
        assert ytdlp_manager._compare_versions("2024.01.01", "2024.02.01") == -1

    def test_different_minor(self):
        assert ytdlp_manager._compare_versions("2024.12.01", "2024.02.01") == 1

    def test_different_patch(self):
        assert ytdlp_manager._compare_versions("2024.02.15", "2024.02.01") == 1

    def test_hyphenated_version(self):
        assert ytdlp_manager._compare_versions("2024-02-01", "2024.01.01") == 1


class TestStateFile:
    """Test state file management."""

    def test_load_empty_state(self, tmp_path, monkeypatch):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        monkeypatch.setenv("OTTERRIP_STATE_DIR", str(state_dir))
        # Reload module to pick up new env
        import importlib
        import otterrip_core.ytdlp_manager as mod
        mod.STATE_DIR = state_dir
        mod.STATE_FILE = state_dir / "ytdlp_state.json"

        state = mod._load_state()
        assert state == {}

    def test_save_and_load_state(self, tmp_path, monkeypatch):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        monkeypatch.setenv("OTTERRIP_STATE_DIR", str(state_dir))

        import otterrip_core.ytdlp_manager as mod
        mod.STATE_DIR = state_dir
        mod.STATE_FILE = state_dir / "ytdlp_state.json"

        test_state = {"last_check": 1234567890, "installed_version": "2024.02.01"}
        mod._save_state(test_state)

        loaded = mod._load_state()
        assert loaded["last_check"] == 1234567890
        assert loaded["installed_version"] == "2024.02.01"

    def test_save_creates_directory(self, tmp_path, monkeypatch):
        state_dir = tmp_path / "new_state"
        monkeypatch.setenv("OTTERRIP_STATE_DIR", str(state_dir))

        import otterrip_core.ytdlp_manager as mod
        mod.STATE_DIR = state_dir
        mod.STATE_FILE = state_dir / "ytdlp_state.json"

        mod._save_state({})
        assert state_dir.exists()
        assert mod.STATE_FILE.exists()


class TestUpdateInterval:
    """Test update interval checking."""

    def test_first_check_always_allowed(self, tmp_path, monkeypatch):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        monkeypatch.setenv("OTTERRIP_STATE_DIR", str(state_dir))

        import otterrip_core.ytdlp_manager as mod
        mod.STATE_DIR = state_dir
        mod.STATE_FILE = state_dir / "ytdlp_state.json"

        assert mod.check_update_interval() is True

    def test_recent_check_blocks(self, tmp_path, monkeypatch):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        monkeypatch.setenv("OTTERRIP_STATE_DIR", str(state_dir))

        import otterrip_core.ytdlp_manager as mod
        mod.STATE_DIR = state_dir
        mod.STATE_FILE = state_dir / "ytdlp_state.json"

        # Set last_check to now
        mod._save_state({"last_check": time.time()})

        assert mod.check_update_interval() is False

    def test_old_check_allows(self, tmp_path, monkeypatch):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        monkeypatch.setenv("OTTERRIP_STATE_DIR", str(state_dir))

        import otterrip_core.ytdlp_manager as mod
        mod.STATE_DIR = state_dir
        mod.STATE_FILE = state_dir / "ytdlp_state.json"

        # Set last_check to 8 days ago
        old_time = time.time() - (8 * 24 * 60 * 60)
        mod._save_state({"last_check": old_time})

        assert mod.check_update_interval() is True


class TestGetVersion:
    """Test version detection."""

    def test_version_not_installed(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            raise FileNotFoundError()

        monkeypatch.setattr(ytdlp_manager.subprocess, "run", fake_run)

        # Mock yt_dlp import to fail
        import builtins
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "yt_dlp":
                raise ImportError("No module named yt_dlp")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", mock_import)

        version = ytdlp_manager.get_version()
        assert version is None

    def test_version_from_subprocess(self, monkeypatch):
        def fake_run(cmd, **kwargs):
            mock_result = MagicMock()
            mock_result.returncode = 0
            mock_result.stdout = "2024.02.01\n"
            return mock_result

        monkeypatch.setattr(ytdlp_manager.subprocess, "run", fake_run)
        version = ytdlp_manager.get_version()
        assert version == "2024.02.01"


class TestFindPip:
    """Test pip detection."""

    def test_find_pip_in_path(self, monkeypatch):
        def fake_which(name):
            if name in ("pip", "pip3"):
                return "/usr/bin/pip3"
            return None

        monkeypatch.setattr(ytdlp_manager.shutil, "which", fake_which)
        pip_cmd = ytdlp_manager._find_pip()
        assert pip_cmd == "/usr/bin/pip3"

    def test_find_pip_not_available(self, monkeypatch):
        def fake_which(name):
            return None

        def fake_run(cmd, **kwargs):
            raise FileNotFoundError()

        monkeypatch.setattr(ytdlp_manager.shutil, "which", fake_which)
        monkeypatch.setattr(ytdlp_manager.subprocess, "run", fake_run)

        pip_cmd = ytdlp_manager._find_pip()
        assert pip_cmd is None


class TestUpdate:
    """Test update functionality."""

    def test_update_no_pip(self, tmp_path, monkeypatch):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        monkeypatch.setenv("OTTERRIP_STATE_DIR", str(state_dir))

        import otterrip_core.ytdlp_manager as mod
        mod.STATE_DIR = state_dir
        mod.STATE_FILE = state_dir / "ytdlp_state.json"

        def fake_which(name):
            return None

        monkeypatch.setattr(mod.shutil, "which", fake_which)
        monkeypatch.setattr(mod, "_find_pip", lambda: None)

        result = mod.update()
        assert result["success"] is False
        assert "pip no encontrado" in result["error"]

    def test_update_success(self, tmp_path, monkeypatch):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        monkeypatch.setenv("OTTERRIP_STATE_DIR", str(state_dir))

        import otterrip_core.ytdlp_manager as mod
        mod.STATE_DIR = state_dir
        mod.STATE_FILE = state_dir / "ytdlp_state.json"

        call_count = [0]

        def fake_run(cmd, **kwargs):
            mock_result = MagicMock()
            if "pip" in str(cmd) and "upgrade" in str(cmd):
                mock_result.returncode = 0
                mock_result.stdout = ""
                mock_result.stderr = ""
            else:
                mock_result.returncode = 0
                mock_result.stdout = "2024.03.01\n"
                mock_result.stderr = ""
            return mock_result

        def fake_which(name):
            return "/usr/bin/pip3"

        def fake_get_version():
            call_count[0] += 1
            if call_count[0] == 1:
                return "2024.02.01"  # Before update
            return "2024.03.01"  # After update

        monkeypatch.setattr(mod.subprocess, "run", fake_run)
        monkeypatch.setattr(mod.shutil, "which", fake_which)
        monkeypatch.setattr(mod, "_find_pip", lambda: "/usr/bin/pip3")
        monkeypatch.setattr(mod, "get_version", fake_get_version)
        monkeypatch.setattr(mod, "get_latest_version", lambda: "2024.03.01")

        result = mod.update(backup=True)
        assert result["success"] is True


class TestRollback:
    """Test rollback functionality."""

    def test_rollback_no_backup(self, tmp_path, monkeypatch):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        monkeypatch.setenv("OTTERRIP_STATE_DIR", str(state_dir))

        import otterrip_core.ytdlp_manager as mod
        mod.STATE_DIR = state_dir
        mod.STATE_FILE = state_dir / "ytdlp_state.json"

        result = mod.rollback()
        assert result["success"] is False
        assert "No hay versión de respaldo" in result["error"]

    def test_rollback_no_pip(self, tmp_path, monkeypatch):
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        monkeypatch.setenv("OTTERRIP_STATE_DIR", str(state_dir))

        import otterrip_core.ytdlp_manager as mod
        mod.STATE_DIR = state_dir
        mod.STATE_FILE = state_dir / "ytdlp_state.json"

        # Set backup version
        mod._save_state({"backup_version": "2024.02.01"})

        monkeypatch.setattr(mod, "_find_pip", lambda: None)

        result = mod.rollback()
        assert result["success"] is False
        assert "pip no encontrado" in result["error"]


class TestEnsureYtDlp:
    """Test ensure_ytdlp functionality."""

    def test_ensure_already_installed(self, monkeypatch):
        monkeypatch.setattr(ytdlp_manager, "get_version", lambda: "2024.03.01")
        result = ytdlp_manager.ensure_ytdlp(min_version="2024.02.01")
        assert result is True

    def test_ensure_old_version(self, monkeypatch):
        monkeypatch.setattr(ytdlp_manager, "get_version", lambda: "2023.01.01")
        # Old version, no pip to install
        monkeypatch.setattr(ytdlp_manager, "_find_pip", lambda: None)
        result = ytdlp_manager.ensure_ytdlp(min_version="2024.02.01")
        assert result is False
