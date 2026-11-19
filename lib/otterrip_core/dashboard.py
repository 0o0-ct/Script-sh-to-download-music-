#!/usr/bin/env python3
"""Textual TUI Dashboard for OtterRip.

Interactive terminal interface showing:
- Downloads: active, pending, completed, failed
- Queue: tasks with pause/resume/cancel
- Library: overview with duplicates

Navigation: keyboard shortcuts (1/2/3 for views, q to quit).
Real-time updates via periodic refresh.

Usage:
    py_core dashboard
    otterrip dashboard
"""

from __future__ import annotations

from datetime import datetime

try:
    from textual import events
    from textual.app import App, ComposeResult
    from textual.containers import Container
    from textual.reactive import reactive
    from textual.screen import Screen
    from textual.widgets import DataTable, Footer, Header, Static, TabPanel, Tabs

    HAS_TEXTUAL = True
except ImportError:
    HAS_TEXTUAL = False

from . import rich_output as ro

# ── Data fetchers ────────────────────────────────────────────────────


def _get_queue_data() -> tuple[list[dict], dict]:
    """Fetch queue data from StateDB."""
    tasks = []
    stats = {}
    try:
        from .state_db import StateDB

        db = StateDB()
        rows = db.queue_list()
        for row in rows:
            tasks.append(
                {
                    "id": row.get("id", "?"),
                    "url": row.get("url", "")[:60],
                    "status": row.get("status", "unknown"),
                    "progress": row.get("progress", 0),
                    "error": row.get("error", "")[:40],
                }
            )
        stats = db.queue_stats()
    except Exception:
        pass
    return tasks, stats


def _get_library_data() -> list[dict]:
    """Fetch library scan data."""
    files = []
    try:
        from .library import LibraryScanner

        scanner = LibraryScanner()
        files = scanner.scan()
    except Exception:
        pass
    return files


def _get_history_data(limit: int = 20) -> list[dict]:
    """Fetch recent history from StateDB."""
    entries = []
    try:
        from .state_db import StateDB

        db = StateDB()
        rows = db.history_list(limit=limit)
        for row in rows:
            entries.append(
                {
                    "title": row.get("title", "?"),
                    "artist": row.get("artist", ""),
                    "status": row.get("status", ""),
                    "time": row.get("timestamp", ""),
                }
            )
    except Exception:
        pass
    return entries


# ── Status colors ────────────────────────────────────────────────────

STATUS_COLORS = {
    "pending": "yellow",
    "running": "blue",
    "paused": "dim",
    "done": "green",
    "failed": "red",
    "cancelled": "grey",
    "partial": "magenta",
}


# ── Dashboard App ────────────────────────────────────────────────────

if HAS_TEXTUAL:

    class QueueView(Static):
        """Queue view with task table."""

        def __init__(self, refresh_interval: int = 5):
            super().__init__()
            self.refresh_interval = refresh_interval

        def on_mount(self) -> None:
            self.set_interval(self.refresh_interval, self._refresh)

        async def _refresh(self) -> None:
            tasks, stats = _get_queue_data()
            if not tasks:
                self.update("  ── Cola de descargas ──\n\n  No hay tareas en la cola.")
                return

            lines = ["  ── Cola de descargas ──\n"]
            for t in tasks[:30]:
                status = t.get("status", "?")
                color = STATUS_COLORS.get(status, "white")
                lines.append(f"  [{color}]{t['id']} [{status}] {t['url']}[/{color}]")

            if stats:
                lines.append(f"\n  Pendientes: {stats.get('pending', 0)}")
                lines.append(f"  En curso: {stats.get('running', 0)}")
                lines.append(f"  Completadas: {stats.get('done', 0)}")
                lines.append(f"  Fallidas: {stats.get('failed', 0)}")

            self.update("\n".join(lines))

        def key_q(self) -> None:
            self.app.exit()

    class DownloadsView(Static):
        """Downloads view with recent history."""

        def __init__(self, refresh_interval: int = 5):
            super().__init__()
            self.refresh_interval = refresh_interval

        def on_mount(self) -> None:
            self.set_interval(self.refresh_interval, self._refresh)

        async def _refresh(self) -> None:
            entries = _get_history_data(limit=20)
            if not entries:
                self.update("  ── Descargas recientes ──\n\n  No hay descargas recientes.")
                return

            lines = ["  ── Descargas recientes ──\n"]
            for e in entries:
                status = e.get("status", "")
                color = STATUS_COLORS.get(status, "white")
                artist = e.get("artist", "")
                title = e.get("title", "?")
                time_str = e.get("time", "")
                display = f"{artist} - {title}" if artist else title
                lines.append(f"  [{color}][{status}] {display}[/{color}]")
                if time_str:
                    lines.append(f"    {time_str}")

            self.update("\n".join(lines))

    class LibraryView(Static):
        """Library view with scan results."""

        def __init__(self, refresh_interval: int = 10):
            super().__init__()
            self.refresh_interval = refresh_interval

        def on_mount(self) -> None:
            self.set_interval(self.refresh_interval, self._refresh)

        async def _refresh(self) -> None:
            files = _get_library_data()
            if not files:
                self.update("  ── Biblioteca musical ──\n\n  No se encontraron archivos de audio.")
                return

            # Count stats
            total = len(files)
            missing_covers = sum(1 for f in files if not f.get("has_cover", True))
            missing_lyrics = sum(1 for f in files if not f.get("has_lyrics", True))
            missing_tags = sum(1 for f in files if not f.get("title", "") or not f.get("artist", ""))

            lines = ["  ── Biblioteca musical ──\n"]
            lines.append(f"  Total archivos: {total}")
            lines.append(f"  Sin portada: {missing_covers}")
            lines.append(f"  Sin letras: {missing_lyrics}")
            lines.append(f"  Sin etiquetas: {missing_tags}")
            lines.append("")

            # Show files with issues
            issues = [f for f in files if not f.get("has_cover") or not f.get("has_lyrics") or not f.get("title")]
            if issues:
                lines.append("  Archivos con problemas:")
                for f in issues[:20]:
                    title = f.get("title", f.get("name", "?"))
                    artist = f.get("artist", "")
                    problems = []
                    if not f.get("has_cover"):
                        problems.append("sin portada")
                    if not f.get("has_lyrics"):
                        problems.append("sin letras")
                    if not f.get("title"):
                        problems.append("sin título")
                    prob_str = ", ".join(problems)
                    display = f"{artist} - {title}" if artist else title
                    lines.append(f"    ⚠ {display} ({prob_str})")

            self.update("\n".join(lines))

    class DashboardApp(App):
        """Main dashboard application with tabs for Downloads, Queue, Library."""

        CSS = """
            Screen {
                layout: grid;
                grid-size: 1;
                grid-rows: 1fr;
            }
            #tabs {
                dock: top;
            }
            TabPanel {
                layout: vertical;
            }
            .content {
                padding: 1;
            }
        """

        BINDINGS = [
            ("1", "switch_downloads", "Descargas"),
            ("2", "switch_queue", "Cola"),
            ("3", "switch_library", "Biblioteca"),
            ("q", "quit", "Salir"),
        ]

        def __init__(self, refresh_interval: int = 5):
            super().__init__()
            self.refresh_interval = refresh_interval

        def compose(self) -> ComposeResult:
            yield Header()
            yield Tabs(
                TabPanel(
                    DownloadsView(self.refresh_interval),
                    id="downloads",
                    name="Descargas",
                ),
                TabPanel(
                    QueueView(self.refresh_interval),
                    id="queue",
                    name="Cola",
                ),
                TabPanel(
                    LibraryView(self.refresh_interval),
                    id="library",
                    name="Biblioteca",
                ),
            )
            yield Footer()

        def action_switch_downloads(self) -> None:
            self.query_one(Tabs).active = "Descargas"

        def action_switch_queue(self) -> None:
            self.query_one(Tabs).active = "Cola"

        def action_switch_library(self) -> None:
            self.query_one(Tabs).active = "Biblioteca"

else:
    """Fallback when Textual is not installed."""

    class DashboardApp:
        """Simple text-based dashboard fallback."""

        def __init__(self, refresh_interval: int = 5):
            self.refresh_interval = refresh_interval

        def run(self):
            """Run a simple text-based dashboard loop."""
            import signal

            running = True

            def handler(sig, frame):
                nonlocal running
                running = False

            signal.signal(signal.SIGINT, handler)

            c = ro.get_console()
            c.print("[bold blue]OtterRip Dashboard (modo texto)[/]\n")
            c.print("  Presiona Ctrl+C para salir\n")

            while running:
                # Clear screen
                print("\033[2J\033[H", end="")

                # Header
                now = datetime.now().strftime("%H:%M:%S")
                print("  ╔══════════════════════════════════════════╗")
                print(f"  ║  OtterRip Dashboard  [{now}]              ║")
                print("  ╚══════════════════════════════════════════╝\n")

                # Queue
                tasks, stats = _get_queue_data()
                print("  ── Cola de descargas ──")
                if tasks:
                    for t in tasks[:15]:
                        status = t.get("status", "?")
                        print(f"    {t['id']} [{status}] {t['url'][:50]}")
                else:
                    print("    No hay tareas")

                if stats:
                    print(f"\n    Pendientes: {stats.get('pending', 0)}")
                    print(f"    En curso: {stats.get('running', 0)}")
                    print(f"    Completadas: {stats.get('done', 0)}")
                    print(f"    Fallidas: {stats.get('failed', 0)}")

                # Recent history
                entries = _get_history_data(limit=10)
                print("\n  ── Descargas recientes ──")
                if entries:
                    for e in entries:
                        status = e.get("status", "")
                        artist = e.get("artist", "")
                        title = e.get("title", "?")
                        display = f"{artist} - {title}" if artist else title
                        print(f"    [{status}] {display}")
                else:
                    print("    No hay descargas recientes")

                print("\n  Presiona Ctrl+C para salir\n")

                try:
                    import time

                    time.sleep(self.refresh_interval)
                except KeyboardInterrupt:
                    break

            print("\n  Dashboard detenido.\n")


def run_dashboard(args) -> int:
    """CLI handler for dashboard subcommand."""
    refresh = getattr(args, "refresh", 5) or 5
    try:
        app = DashboardApp(refresh_interval=refresh)
        app.run()
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as e:
        ro.print_error(f"Error al iniciar dashboard: {e}")
        return 1
