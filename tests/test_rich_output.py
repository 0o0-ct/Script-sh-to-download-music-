#!/usr/bin/env python3
"""Tests for rich_output.py — Rich output utilities."""
import sys
import pytest


class TestHasRich:
    """Test HAS_RICH flag."""

    def test_has_rich_attribute(self):
        from otterrip_core import rich_output
        assert hasattr(rich_output, "HAS_RICH")
        assert isinstance(rich_output.HAS_RICH, bool)


class TestConsole:
    """Test get_console and fallback."""

    def test_get_console_returns_console(self):
        from otterrip_core import rich_output
        c = rich_output.get_console()
        assert c is not None
        assert hasattr(c, "print")

    def test_get_console_is_singleton(self):
        from otterrip_core import rich_output
        # Reset singleton
        rich_output._console = None
        c1 = rich_output.get_console()
        c2 = rich_output.get_console()
        assert c1 is c2

    def test_plain_console_print(self):
        from otterrip_core.rich_output import _PlainConsole
        pc = _PlainConsole()
        # Should not raise
        pc.print("test")
        pc.log("test")
        pc.rule("title")
        pc.print_exception()


class TestProgressManager:
    """Test ProgressManager context manager."""

    def test_progress_manager_enter_exit(self):
        from otterrip_core.rich_output import ProgressManager
        pm = ProgressManager()
        with pm as progress:
            assert progress is not None
        # No exception on exit

    def test_progress_manager_add_task(self):
        from otterrip_core.rich_output import ProgressManager
        pm = ProgressManager()
        with pm:
            task_id = pm.add_task("test", total=100)
            assert isinstance(task_id, int)

    def test_progress_manager_update(self):
        from otterrip_core.rich_output import ProgressManager
        pm = ProgressManager()
        with pm:
            task_id = pm.add_task("test", total=100)
            pm.update(task_id, advance=10)
            # No exception

    def test_progress_manager_remove_task(self):
        from otterrip_core.rich_output import ProgressManager
        pm = ProgressManager()
        with pm:
            task_id = pm.add_task("test", total=100)
            pm.remove_task(task_id)
            # No exception


class TestMultiProgress:
    """Test MultiProgress context manager."""

    def test_multi_progress_enter_exit(self):
        from otterrip_core.rich_output import MultiProgress
        mp = MultiProgress()
        with mp as progress:
            assert progress is not None

    def test_multi_progress_add_task(self):
        from otterrip_core.rich_output import MultiProgress
        mp = MultiProgress()
        with mp:
            task_id = mp.add_task("Song 1", total=1.0)
            assert isinstance(task_id, int)


class TestTableRenderer:
    """Test TableRenderer."""

    def test_render_creates_table(self):
        from otterrip_core.rich_output import TableRenderer
        tr = TableRenderer(title="Test")
        table = tr.render(["A", "B"], [["1", "2"], ["3", "4"]])
        assert table is not None

    def test_render_empty_rows(self):
        from otterrip_core.rich_output import TableRenderer
        tr = TableRenderer()
        table = tr.render(["A", "B"], [])
        assert table is not None

    def test_print_table_no_exception(self):
        from otterrip_core.rich_output import TableRenderer
        tr = TableRenderer()
        tr.print_table(["A", "B"], [["1", "2"]])
        # No exception


class TestPanelRenderer:
    """Test PanelRenderer."""

    def test_render_creates_panel(self):
        from otterrip_core.rich_output import PanelRenderer
        panel = PanelRenderer.render("Title", "Content")
        assert panel is not None

    def test_print_panel_no_exception(self):
        from otterrip_core.rich_output import PanelRenderer
        PanelRenderer.print_panel("Title", "Content")
        # No exception


class TestLogRenderer:
    """Test LogRenderer colored output."""

    def test_log_levels(self):
        from otterrip_core.rich_output import LogRenderer
        for level in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]:
            LogRenderer.log(level, "test message")
        # No exception

    def test_success(self):
        from otterrip_core.rich_output import LogRenderer
        LogRenderer.success("done")

    def test_error(self):
        from otterrip_core.rich_output import LogRenderer
        LogRenderer.error("failed")

    def test_warning(self):
        from otterrip_core.rich_output import LogRenderer
        LogRenderer.warning("caution")

    def test_info(self):
        from otterrip_core.rich_output import LogRenderer
        LogRenderer.info("note")

    def test_spinner(self):
        from otterrip_core.rich_output import LogRenderer
        spinner = LogRenderer.spinner("Working...")
        # May be None if Rich not installed


class TestConvenienceFunctions:
    """Test convenience functions."""

    def test_print_panel(self):
        from otterrip_core.rich_output import print_panel
        print_panel("Title", "Content")

    def test_print_table(self):
        from otterrip_core.rich_output import print_table
        print_table(["A"], [["1"]])

    def test_print_success(self):
        from otterrip_core.rich_output import print_success
        print_success("ok")

    def test_print_error(self):
        from otterrip_core.rich_output import print_error
        print_error("fail")

    def test_print_warning(self):
        from otterrip_core.rich_output import print_warning
        print_warning("warn")

    def test_print_info(self):
        from otterrip_core.rich_output import print_info
        print_info("info")
