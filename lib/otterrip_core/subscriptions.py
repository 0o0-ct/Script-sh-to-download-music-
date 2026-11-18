#!/usr/bin/env python3
"""Playlist subscriptions for OtterRip.

Watch playlists for new songs and automatically download them.
Integrates with systemd user timers for periodic checks.

Uses StateDB for persistent storage of subscription data.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

from otterrip_core import state_db


class SubscriptionManager:
    """Manage playlist subscriptions."""

    def __init__(self, state_dir: str = ""):
        """Initialize with a state directory.

        Args:
            state_dir: Directory for StateDB. Uses OTTERRIP_STATE_DIR env var if not set.
        """
        self.state_dir = state_dir or os.environ.get("OTTERRIP_STATE_DIR", "")
        if not self.state_dir:
            self.state_dir = str(Path.home() / ".config" / "otterrip")
        self.db = state_db.StateDB(self.state_dir)

    def add(self, url: str, interval: int = 86400, enabled: bool = True) -> int:
        """Add a subscription to watch a playlist.

        Args:
            url: YouTube playlist URL to watch.
            interval: Check interval in seconds (default: 86400 = 24 hours).
            enabled: Whether the subscription is active.

        Returns:
            Subscription ID.
        """
        return self.db.subscription_add(url, interval=interval, enabled=enabled)

    def remove(self, url: str) -> bool:
        """Remove a subscription by URL.

        Args:
            url: URL of the subscription to remove.

        Returns:
            True if removed, False if not found.
        """
        return self.db.subscription_remove(url)

    def list_subscriptions(self) -> list[dict]:
        """List all subscriptions.

        Returns:
            List of subscription dicts.
        """
        return self.db.subscription_list()

    def check(self, url: str = "", download: bool = False) -> dict:
        """Check subscriptions for new songs.

        Args:
            url: Specific URL to check. If empty, check all enabled subscriptions.
            download: If True, download new songs using download_engine.

        Returns:
            Dict with 'checked', 'new_songs', 'errors' lists.
        """
        subscriptions = self.db.subscription_list()
        if url:
            subscriptions = [s for s in subscriptions if s["url"] == url]

        checked = []
        new_songs = []
        errors = []

        for sub in subscriptions:
            if not sub.get("enabled", True):
                continue

            sub_url = sub["url"]
            interval = sub.get("interval", 86400)
            last_check = sub.get("last_check", 0) or 0

            # Check if enough time has passed
            if (time.time() - last_check) < interval:
                continue

            try:
                # Use playlist_manager to sync
                from otterrip_core.playlist_manager import PlaylistManager

                manager = PlaylistManager()
                result = manager.sync(sub_url, "")

                new_count = len(result.get("new", []))
                new_songs.extend(result.get("new", []))

                # Update last check time
                self.db.subscription_update_check(sub_url)
                checked.append({
                    "url": sub_url,
                    "new": new_count,
                })

                # Download new songs if requested
                if download and new_count > 0:
                    for song in result.get("new", []):
                        song_url = song.get("url", "")
                        if song_url:
                            try:
                                self._download_song(song_url)
                            except Exception as exc:
                                errors.append({
                                    "url": song_url,
                                    "error": str(exc),
                                })

            except Exception as exc:
                errors.append({
                    "url": sub_url,
                    "error": str(exc),
                })

            # Update last check even on error to avoid spam
            self.db.subscription_update_check(sub_url)
            if sub_url not in [c["url"] for c in checked]:
                checked.append({
                    "url": sub_url,
                    "new": 0,
                    "error": errors[-1]["error"] if errors else None,
                })

        return {
            "checked": checked,
            "new_songs": new_songs,
            "errors": errors,
        }

    def _download_song(self, url: str) -> None:
        """Download a single song using download_engine."""
        try:
            from otterrip_core import download_engine

            dest = os.environ.get("OTTERRIP_DOWNLOAD_DIR", str(Path.home() / "Música"))
            engine = download_engine.DownloadEngine(
                dest=dest,
                format_profile="mp3",
                workers=1,
            )
            engine.download(url)
        except Exception:
            # Fallback: use subprocess to call yt-dlp directly
            subprocess.run(
                ["yt-dlp", "-x", "--audio-format", "mp3", "-o",
                 os.path.join(dest, "%(artist)s - %(title)s.%(ext)s"), url],
                timeout=300,
            )

    def generate_systemd_timer(self, enable: bool = True) -> dict:
        """Generate systemd user timer files for periodic checks.

        Args:
            enable: If True, enable the timer. If False, disable it.

        Returns:
            Dict with 'service_file', 'timer_file', 'enabled' status.
        """
        config_dir = Path.home() / ".config" / "systemd" / "user"
        result = {
            "service_file": "",
            "timer_file": "",
            "enabled": False,
        }

        # Check if systemd is available
        if not shutil.which("systemctl"):
            print("  systemd no disponible. Saltando generación de timer.", file=sys.stderr)
            return result

        config_dir.mkdir(parents=True, exist_ok=True)

        # Find py_core script path
        py_core = shutil.which("py_core")
        if not py_core:
            # Try to find it in the project
            project_dir = Path(__file__).parent.parent.parent
            py_core_path = project_dir / "py_core"
            if py_core_path.is_file():
                py_core = str(py_core_path)
            else:
                print("  py_core no encontrado en el PATH.", file=sys.stderr)
                return result

        # Service file
        service_content = f"""[Unit]
Description=OtterRip Playlist Subscription Check
After=network-online.target

[Service]
Type=oneshot
ExecStart={py_core} subscriptions check --download
Environment=OTTERRIP_STATE_DIR={self.state_dir}
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=timers.target
"""
        service_file = config_dir / "otterrip-subscriptions.service"
        service_file.write_text(service_content, encoding="utf-8")
        result["service_file"] = str(service_file)

        # Timer file
        timer_content = """[Unit]
Description=OtterRip Playlist Subscription Check Timer

[Timer]
OnCalendar=daily
Persistent=true
RandomizedDelaySec=3600

[Install]
WantedBy=timers.target
"""
        timer_file = config_dir / "otterrip-subscriptions.timer"
        timer_file.write_text(timer_content, encoding="utf-8")
        result["timer_file"] = str(timer_file)

        # Enable/disable the timer
        try:
            if enable:
                subprocess.run(
                    ["systemctl", "--user", "daemon-reload"],
                    check=True, timeout=10,
                )
                subprocess.run(
                    ["systemctl", "--user", "enable", "--now", "otterrip-subscriptions.timer"],
                    check=True, timeout=10,
                )
                result["enabled"] = True
            else:
                subprocess.run(
                    ["systemctl", "--user", "disable", "--now", "otterrip-subscriptions.timer"],
                    check=True, timeout=10,
                )
                result["enabled"] = False
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, FileNotFoundError):
            print("  Error al configurar systemd timer.", file=sys.stderr)
            result["enabled"] = False

        return result


def run_subscriptions(args) -> int:
    """CLI handler for subscriptions subcommands."""
    cmd = getattr(args, "subscriptions_cmd", None)
    if not cmd:
        print("Usage: subscriptions {add|remove|list|check|timer}")
        return 1

    manager = SubscriptionManager()

    if cmd == "add":
        url = getattr(args, "url", "")
        if not url:
            print("Error: se requiere una URL", file=sys.stderr)
            return 1
        interval = getattr(args, "interval", 86400) or 86400
        sub_id = manager.add(url, interval=interval)
        print(f"Suscripción añadida (ID: {sub_id}): {url}")
        print(f"Intervalo de verificación: {interval}s ({interval // 3600}h)")
        return 0

    elif cmd == "remove":
        url = getattr(args, "url", "")
        if not url:
            print("Error: se requiere una URL", file=sys.stderr)
            return 1
        if manager.remove(url):
            print(f"Suscripción eliminada: {url}")
            return 0
        else:
            print(f"Suscripción no encontrada: {url}", file=sys.stderr)
            return 1

    elif cmd == "list":
        subs = manager.list_subscriptions()
        if not subs:
            print("No hay suscripciones registradas.")
            return 0

        if getattr(args, "json", False):
            print(json.dumps(subs, ensure_ascii=False, default=str))
        else:
            print()
            print("  ── Suscripciones activas ──")
            print()
            for sub in subs:
                url = sub.get("url", "?")
                interval = sub.get("interval", 86400)
                enabled = sub.get("enabled", True)
                last_check = sub.get("last_check", 0) or 0
                status = "✓" if enabled else "✗"
                interval_str = f"{interval // 3600}h" if interval >= 3600 else f"{interval // 60}m"

                # Calculate time since last check
                if last_check:
                    elapsed = time.time() - last_check
                    if elapsed < 3600:
                        time_str = f"hace {int(elapsed // 60)}m"
                    elif elapsed < 86400:
                        time_str = f"hace {int(elapsed // 3600)}h"
                    else:
                        time_str = f"hace {int(elapsed // 86400)}d"
                else:
                    time_str = "nunca"

                print(f"  {status} {url}")
                print(f"    Intervalo: {interval_str} | Última verificación: {time_str}")
            print()
            print(f"  Total: {len(subs)} suscripción(es)")

        return 0

    elif cmd == "check":
        url = getattr(args, "url", "") or ""
        download = getattr(args, "download", False)

        result = manager.check(url=url, download=download)

        checked_count = len(result["checked"])
        new_count = len(result["new_songs"])
        error_count = len(result["errors"])

        print()
        print("  ── Verificación de suscripciones ──")
        print()
        print(f"  Suscripciones verificadas: {checked_count}")
        print(f"  Canciones nuevas:          {new_count}")
        print(f"  Errores:                   {error_count}")
        print()

        if new_count > 0:
            print("  Canciones nuevas:")
            for song in result["new_songs"][:20]:
                title = song.get("title", "?")
                artist = song.get("artist", "")
                print(f"    • {artist} - {title}" if artist else f"    • {title}")
            if new_count > 20:
                print(f"    ... y {new_count - 20} más")

        if download and new_count > 0:
            print()
            print("  Descargando canciones nuevas...")

        if error_count > 0:
            print()
            print("  Errores:")
            for err in result["errors"][:10]:
                print(f"    • {err['url']}: {err['error']}")

        print()

        if getattr(args, "json", False):
            print(json.dumps(result, ensure_ascii=False, default=str))

        return 0 if error_count == 0 else 1

    elif cmd == "timer":
        enable = getattr(args, "enable", True)
        result = manager.generate_systemd_timer(enable=enable)

        if result["service_file"]:
            action = "habilitado" if result["enabled"] else "deshabilitado"
            print(f"Systemd timer {action}.")
            print(f"  Servicio: {result['service_file']}")
            print(f"  Timer:    {result['timer_file']}")
        else:
            print("No se pudo generar el timer (systemd no disponible).")

        return 0 if result["service_file"] else 1

    else:
        print(f"Unknown subscriptions command: {cmd}", file=sys.stderr)
        return 1
