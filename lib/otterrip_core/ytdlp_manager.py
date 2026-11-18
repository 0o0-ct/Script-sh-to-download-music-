#!/usr/bin/env python3
"""yt-dlp version manager for OtterRip.

Manages installation, version checking, update policy, and rollback
for yt-dlp. Uses a conservative update policy: check for new versions
every 7 days, allow update from diagnostic menu, keep backup version
for rollback.

State file: ~/.config/otterrip/ytdlp_state.json
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

STATE_DIR = Path(os.environ.get("OTTERRIP_STATE_DIR", Path.home() / ".config" / "otterrip"))
STATE_FILE = STATE_DIR / "ytdlp_state.json"
UPDATE_CHECK_INTERVAL = 7 * 24 * 60 * 60  # 7 days in seconds


def _load_state() -> dict:
    """Load yt-dlp state from disk."""
    if STATE_FILE.is_file():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_state(state: dict) -> None:
    """Save yt-dlp state to disk."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def get_version() -> Optional[str]:
    """Get the currently installed yt-dlp version.

    Returns:
        Version string (e.g. "2024.02.01") or None if not installed.
    """
    # Try yt-dlp --version first
    for cmd in [["yt-dlp", "--version"], ["yt-dlp", "-v"]]:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                version = result.stdout.strip().split()[0]
                # yt-dlp version format: 2024.02.01 or similar
                if re.match(r"\d{4}\.\d{1,2}\.\d{1,2}", version):
                    return version
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue

    # Try Python package
    try:
        import yt_dlp
        return getattr(yt_dlp, "__version__", None)
    except ImportError:
        pass

    return None


def get_latest_version() -> Optional[str]:
    """Get the latest yt-dlp version from PyPI.

    Returns:
        Latest version string or None if check fails.
    """
    # Try PyPI JSON API
    try:
        import urllib.request
        import urllib.error

        req = urllib.request.Request(
            "https://pypi.org/pypi/yt-dlp/json",
            headers={"User-Agent": "OtterRip/7.7.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            data = json.loads(response.read().decode("utf-8"))
            return data.get("info", {}).get("version")
    except (urllib.error.URLError, json.JSONDecodeError, OSError):
        pass

    # Fallback: pip index versions
    try:
        result = subprocess.run(
            ["pip", "index", "versions", "yt-dlp"],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            # Output: "yt-dlp (<version>)"
            match = re.search(r"yt-dlp \(([^)]+)\)", result.stdout)
            if match:
                return match.group(1)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return None


def is_update_available() -> bool:
    """Check if a newer version of yt-dlp is available.

    Returns:
        True if update is available, False otherwise.
    """
    current = get_version()
    if not current:
        return True  # Not installed, so "update" means install

    latest = get_latest_version()
    if not latest:
        return False  # Can't check, assume up to date

    # Compare version strings (yt-dlp uses date-based versions)
    return _compare_versions(latest, current) > 0


def _compare_versions(v1: str, v2: str) -> int:
    """Compare two version strings.

    Returns:
        -1 if v1 < v2, 0 if equal, 1 if v1 > v2.
    """
    # yt-dlp versions are date-based: 2024.02.01
    # Split by dots and compare numerically
    parts1 = [int(x) for x in re.split(r"[.\-]", v1) if x.isdigit()]
    parts2 = [int(x) for x in re.split(r"[.\-]", v2) if x.isdigit()]

    # Pad shorter list with zeros
    max_len = max(len(parts1), len(parts2))
    parts1.extend([0] * (max_len - len(parts1)))
    parts2.extend([0] * (max_len - len(parts2)))

    for a, b in zip(parts1, parts2):
        if a < b:
            return -1
        if a > b:
            return 1
    return 0


def check_update_interval() -> bool:
    """Check if enough time has passed since last version check.

    Returns:
        True if it's time to check for updates (7 days passed or first check).
    """
    state = _load_state()
    last_check = state.get("last_check", 0)
    now = time.time()
    return (now - last_check) >= UPDATE_CHECK_INTERVAL


def _update_last_check() -> None:
    """Update the last check timestamp in state file."""
    state = _load_state()
    state["last_check"] = time.time()
    state["installed_version"] = get_version() or "unknown"
    _save_state(state)


def update(backup: bool = True) -> dict:
    """Update yt-dlp to the latest version.

    Args:
        backup: If True, backup current version before updating.

    Returns:
        Dict with update result: {"success": bool, "old_version": str, "new_version": str, "error": str}
    """
    result = {
        "success": False,
        "old_version": get_version() or "not installed",
        "new_version": "",
        "error": "",
    }

    # Backup current version if requested
    if backup:
        current_version = get_version()
        if current_version:
            state = _load_state()
            state["backup_version"] = current_version
            _save_state(state)

    # Find pip to use
    pip_cmd = _find_pip()
    if not pip_cmd:
        result["error"] = "pip no encontrado. Instala pip para actualizar yt-dlp."
        return result

    # Get latest version
    latest = get_latest_version()
    if latest:
        result["new_version"] = latest

    # Run pip install --upgrade
    try:
        cmd = [pip_cmd, "install", "--upgrade", "yt-dlp"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        if proc.returncode == 0:
            new_version = get_version()
            result["success"] = True
            result["new_version"] = new_version or "unknown"
            _update_last_check()
        else:
            error_msg = proc.stderr.strip()[:500] if proc.stderr else proc.stdout.strip()[:500]
            result["error"] = error_msg or "pip install failed"
    except subprocess.TimeoutExpired:
        result["error"] = "Timeout durante la actualización"
    except FileNotFoundError:
        result["error"] = f"pip no encontrado en el PATH"

    return result


def rollback() -> dict:
    """Rollback yt-dlp to the backup version.

    Returns:
        Dict with rollback result: {"success": bool, "restored_version": str, "error": str}
    """
    result = {
        "success": False,
        "restored_version": "",
        "error": "",
    }

    state = _load_state()
    backup_version = state.get("backup_version")

    if not backup_version:
        result["error"] = "No hay versión de respaldo disponible"
        return result

    pip_cmd = _find_pip()
    if not pip_cmd:
        result["error"] = "pip no encontrado"
        return result

    try:
        cmd = [pip_cmd, "install", f"yt-dlp=={backup_version}"]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        if proc.returncode == 0:
            restored = get_version()
            result["success"] = True
            result["restored_version"] = restored or backup_version
            # Clear backup after successful rollback
            state["backup_version"] = ""
            _save_state(state)
        else:
            error_msg = proc.stderr.strip()[:500] if proc.stderr else proc.stdout.strip()[:500]
            result["error"] = error_msg or "pip install failed"
    except subprocess.TimeoutExpired:
        result["error"] = "Timeout durante el rollback"
    except FileNotFoundError:
        result["error"] = "pip no encontrado"

    return result


def _find_pip() -> Optional[str]:
    """Find pip executable, preferring the one in the current Python environment."""
    # Try to find pip associated with current Python
    python = sys.executable if 'sys' in globals() else None
    if python:
        import sys as _sys
        python = _sys.executable

    # Check common pip locations
    for pip_name in ["pip", "pip3", "pip-3.11", "pip-3.12"]:
        pip_path = shutil.which(pip_name)
        if pip_path:
            return pip_path

    # Try python -m pip
    if python:
        result = subprocess.run(
            [python, "-m", "pip", "--version"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return f"{python} -m pip"

    return None


def ensure_ytdlp(min_version: str = "2024.02.01") -> bool:
    """Ensure yt-dlp is installed and meets minimum version.

    Args:
        min_version: Minimum required version string.

    Returns:
        True if yt-dlp meets requirements, False otherwise.
    """
    current = get_version()
    if current and _compare_versions(current, min_version) >= 0:
        return True

    # Try to install/upgrade
    if not current:
        pip_cmd = _find_pip()
        if pip_cmd:
            try:
                subprocess.run(
                    [pip_cmd, "install", "yt-dlp"],
                    capture_output=True, text=True, timeout=300
                )
                return get_version() is not None
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass
    return False


if __name__ == "__main__":
    import sys as _sys

    if len(_sys.argv) < 2:
        print("Usage: ytdlp_manager.py [version|update-check|update|rollback]")
        _sys.exit(1)

    action = _sys.argv[1]

    if action == "version":
        v = get_version()
        print(f"yt-dlp version: {v or 'not installed'}")
    elif action == "update-check":
        if check_update_interval():
            available = is_update_available()
            _update_last_check()
            current = get_version() or "unknown"
            latest = get_latest_version() or "unknown"
            print(f"Installed: {current}")
            print(f"Latest: {latest}")
            print(f"Update available: {available}")
            _sys.exit(0 if available else 1)
        else:
            state = _load_state()
            last_check = state.get("last_check", 0)
            days_left = (UPDATE_CHECK_INTERVAL - (time.time() - last_check)) / (24 * 60 * 60)
            print(f"Last check was {days_left:.1f} days ago. Next check in {days_left:.1f} days.")
            _sys.exit(1)
    elif action == "update":
        result = update(backup=True)
        if result["success"]:
            print(f"Updated: {result['old_version']} → {result['new_version']}")
        else:
            print(f"Update failed: {result['error']}")
            _sys.exit(1)
    elif action == "rollback":
        result = rollback()
        if result["success"]:
            print(f"Rolled back to: {result['restored_version']}")
        else:
            print(f"Rollback failed: {result['error']}")
            _sys.exit(1)
    else:
        print(f"Unknown action: {action}")
        _sys.exit(1)
