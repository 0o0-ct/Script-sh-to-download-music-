#!/usr/bin/env python3
"""Rich output utilities for OtterRip.

Provides shared Rich components for tables, progress bars, panels,
and colored log output across the CLI and dashboard.
"""
from __future__ import annotations

import sys
from typing import Any, Optional

try:
    from rich.console import Console
    from rich.progress import (
        Progress,
        SpinnerColumn,
        TextColumn,
        BarColumn,
        DownloadColumn,
        TransferSpeedColumn,
        TimeRemainingColumn,
        MofNCompleteColumn,
    )
    from rich.table import Table
    from rich.panel import Panel
    from rich.columns import Columns
    from rich.text import Text
    from rich.live import Live
    from rich.logging import RichHandler
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

# ── Console ──────────────────────────────────────────────────────────

_console: Optional["Console"] = None


def get_console() -> Any:
    """Return a shared Rich Console instance, or a plain fallback."""
    global _console
    if _console is None:
        if HAS_RICH:
            _console = Console()
        else:
            # Fallback: return a simple object with print()
            _console = _PlainConsole()
    return _console


class _PlainConsole:
    """Minimal fallback when Rich is not installed."""

    def print(self, *args: Any, **kwargs: Any) -> None:
        print(*args)

    def log(self, *args: Any, **kwargs: Any) -> None:
        print(*args)

    def rule(self, title: str = "") -> None:
        if title:
            print(f"\n{'─' * 40}\n  {title}\n{'─' * 40}\n")

    def print_exception(self, **kwargs: Any) -> None:
        import traceback
        traceback.print_exc()


# ── Progress Manager ─────────────────────────────────────────────────

class ProgressManager:
    """Shared progress bar for downloads.

    Usage:
        pm = ProgressManager()
        with pm as progress:
            task_id = pm.add_task("Downloading...", total=100)
            for i in range(100):
                pm.update(task_id, advance=1)
    """

    def __init__(self, transient: bool = True):
        self._progress: Optional[Progress] = None
        self._live: Optional[Live] = None
        self._transient = transient
        self._tasks: dict[str, int] = {}  # name -> task_id

    def __enter__(self) -> "Progress":
        if HAS_RICH:
            columns = [
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                DownloadColumn(),
                TransferSpeedColumn(),
                TimeRemainingColumn(),
            ]
            self._progress = Progress(*columns, transient=self._transient)
            self._live = self._progress
            self._progress.start()
            return self._progress
        else:
            return self

    def __exit__(self, *exc: Any) -> None:
        if self._progress:
            self._progress.stop()

    def add_task(self, description: str, total: float = 100) -> int:
        """Add a progress task. Returns the task ID."""
        if HAS_RICH and self._progress:
            task_id = self._progress.add_task(description, total=total)
            self._tasks[description] = task_id
            return task_id
        return 0

    def update(self, task_id: int, **kwargs: Any) -> None:
        """Update a task's progress."""
        if HAS_RICH and self._progress:
            self._progress.update(task_id, **kwargs)

    def remove_task(self, task_id: int) -> None:
        """Remove a task."""
        if HAS_RICH and self._progress:
            self._progress.remove_task(task_id)

    def update(self, task_id: int, description: str = "", **kwargs: Any) -> None:
        """Update a task's description and/or progress."""
        if HAS_RICH and self._progress:
            self._progress.update(task_id, description=description, **kwargs)


class MultiProgress:
    """Progress for multiple downloads with MofNCompleteColumn.

    Usage:
        mp = MultiProgress()
        with mp as progress:
            task_id = mp.add_task("Song 1", total=1.0)
            # ... update tasks ...
    """

    def __init__(self):
        self._progress: Optional[Progress] = None

    def __enter__(self) -> "Progress":
        if HAS_RICH:
            columns = [
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                MofNCompleteColumn(),
                TimeRemainingColumn(),
            ]
            self._progress = Progress(*columns, transient=True)
            self._progress.start()
            return self._progress
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._progress:
            self._progress.stop()

    def add_task(self, description: str, total: float = 1.0) -> int:
        if HAS_RICH and self._progress:
            return self._progress.add_task(description, total=total)
        return 0

    def update(self, task_id: int, **kwargs: Any) -> None:
        if HAS_RICH and self._progress:
            self._progress.update(task_id, **kwargs)

    def remove_task(self, task_id: int) -> None:
        if HAS_RICH and self._progress:
            self._progress.remove_task(task_id)


# ── Table Renderer ───────────────────────────────────────────────────

class TableRenderer:
    """Render tabular data with Rich tables."""

    def __init__(self, title: str = ""):
        self.title = title

    def render(self, headers: list[str], rows: list[list[str]],
               show_header: bool = True) -> Table:
        """Create a Rich Table with headers and rows."""
        table = Table(title=self.title, show_header=show_header,
                      header_style="bold magenta", border_style="blue")
        for h in headers:
            table.add_column(h, overflow="fold")
        for row in rows:
            table.add_row(*[str(c) for c in row])
        return table

    def print_table(self, headers: list[str], rows: list[list[str]],
                    console: Optional[Any] = None) -> None:
        """Print a table directly to console."""
        c = console or get_console()
        table = self.render(headers, rows)
        c.print(table)


# ── Panel Renderer ───────────────────────────────────────────────────

class PanelRenderer:
    """Render Rich panels for dashboard sections."""

    @staticmethod
    def render(title: str, content: str, style: str = "bold blue",
               expand: bool = True) -> Panel:
        """Create a Rich Panel."""
        return Panel(content, title=title, style=style, expand=expand)

    @staticmethod
    def print_panel(title: str, content: str, style: str = "bold blue",
                    console: Optional[Any] = None) -> None:
        """Print a panel directly."""
        c = console or get_console()
        panel = PanelRenderer.render(title, content, style)
        c.print(panel)

    @staticmethod
    def print_columns(panels: list[Panel], console: Optional[Any] = None) -> None:
        """Print multiple panels side by side."""
        c = console or get_console()
        cols = Columns(panels, equal=True, expand=True)
        c.print(cols)


# ── Log Renderer ─────────────────────────────────────────────────────

class LogRenderer:
    """Colored log output with Rich."""

    LEVEL_COLORS = {
        "DEBUG": "dim white",
        "INFO": "green",
        "WARNING": "yellow",
        "ERROR": "red",
        "CRITICAL": "bold red",
    }

    @staticmethod
    def log(level: str, message: str, console: Optional[Any] = None) -> None:
        """Print a colored log line."""
        c = console or get_console()
        color = LogRenderer.LEVEL_COLORS.get(level.upper(), "white")
        c.print(f"[{color}][bold]{level}[/][/{color}] {message}")

    @staticmethod
    def success(message: str, console: Optional[Any] = None) -> None:
        c = console or get_console()
        c.print(f"[bold green]✓[/] {message}")

    @staticmethod
    def error(message: str, console: Optional[Any] = None) -> None:
        c = console or get_console()
        c.print(f"[bold red]✗[/] {message}")

    @staticmethod
    def warning(message: str, console: Optional[Any] = None) -> None:
        c = console or get_console()
        c.print(f"[bold yellow]⚠[/] {message}")

    @staticmethod
    def info(message: str, console: Optional[Any] = None) -> None:
        c = console or get_console()
        c.print(f"[bold blue]ℹ[/] {message}")

    @staticmethod
    def spinner(text: str = "Working...") -> Any:
        """Return a Rich spinner context manager."""
        if HAS_RICH:
            from rich.spinner import Spinner
            return Spinner("dots", text)
        return None


# ── Convenience functions ────────────────────────────────────────────

def print_panel(title: str, content: str) -> None:
    """Quick panel print."""
    c = get_console()
    c.print(Panel(content, title=title, style="bold blue"))


def print_table(headers: list[str], rows: list[list[str]],
                title: str = "") -> None:
    """Quick table print."""
    renderer = TableRenderer(title=title)
    renderer.print_table(headers, rows)


def print_success(message: str) -> None:
    LogRenderer.success(message)


def print_error(message: str) -> None:
    LogRenderer.error(message)


def print_warning(message: str) -> None:
    LogRenderer.warning(message)


def print_info(message: str) -> None:
    LogRenderer.info(message)
