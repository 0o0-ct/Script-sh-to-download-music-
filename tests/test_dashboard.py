#!/usr/bin/env python3
"""Tests for dashboard.py — Textual TUI dashboard."""
import pytest
from unittest.mock import patch, MagicMock


class TestDashboardImports:
    """Test that dashboard.py imports correctly."""

    def test_dashboard_module_imports(self):
        from otterrip_core import dashboard
        assert hasattr(dashboard, "HAS_TEXTUAL")
        assert hasattr(dashboard, "DashboardApp")
        assert hasattr(dashboard, "run_dashboard")

    def test_has_textual_attribute(self):
        from otterrip_core.dashboard import HAS_TEXTUAL
        assert isinstance(HAS_TEXTUAL, bool)


class TestDashboardApp:
    """Test DashboardApp class."""

    def test_dashboard_app_exists(self):
        from otterrip_core.dashboard import DashboardApp
        assert DashboardApp is not None

    def test_dashboard_app_init(self):
        from otterrip_core.dashboard import DashboardApp
        app = DashboardApp(refresh_interval=5)
        assert app.refresh_interval == 5

    def test_dashboard_app_default_refresh(self):
        from otterrip_core.dashboard import DashboardApp
        app = DashboardApp()
        assert app.refresh_interval == 5


class TestDataFetchers:
    """Test data fetcher functions."""

    def test_get_queue_data_returns_tuple(self):
        from otterrip_core.dashboard import _get_queue_data
        tasks, stats = _get_queue_data()
        assert isinstance(tasks, list)
        assert isinstance(stats, dict)

    def test_get_library_data_returns_list(self):
        from otterrip_core.dashboard import _get_library_data
        files = _get_library_data()
        assert isinstance(files, list)

    def test_get_history_data_returns_list(self):
        from otterrip_core.dashboard import _get_history_data
        entries = _get_history_data(limit=10)
        assert isinstance(entries, list)

    def test_get_history_data_limit(self):
        from otterrip_core.dashboard import _get_history_data
        entries = _get_history_data(limit=5)
        assert isinstance(entries, list)


class TestStatusColors:
    """Test STATUS_COLORS mapping."""

    def test_status_colors_defined(self):
        from otterrip_core.dashboard import STATUS_COLORS
        assert isinstance(STATUS_COLORS, dict)

    def test_status_colors_has_pending(self):
        from otterrip_core.dashboard import STATUS_COLORS
        assert "pending" in STATUS_COLORS

    def test_status_colors_has_running(self):
        from otterrip_core.dashboard import STATUS_COLORS
        assert "running" in STATUS_COLORS

    def test_status_colors_has_done(self):
        from otterrip_core.dashboard import STATUS_COLORS
        assert "done" in STATUS_COLORS

    def test_status_colors_has_failed(self):
        from otterrip_core.dashboard import STATUS_COLORS
        assert "failed" in STATUS_COLORS


class TestRunDashboard:
    """Test run_dashboard CLI handler."""

    def test_run_dashboard_with_mock_args(self):
        from otterrip_core.dashboard import run_dashboard
        from types import SimpleNamespace

        args = SimpleNamespace(refresh=5)

        with patch("otterrip_core.dashboard.DashboardApp") as MockApp:
            mock_instance = MagicMock()
            mock_instance.run.return_value = None
            MockApp.return_value = mock_instance

            result = run_dashboard(args)
            assert result == 0
            mock_instance.run.assert_called_once()

    def test_run_dashboard_keyboard_interrupt(self):
        from otterrip_core.dashboard import run_dashboard
        from types import SimpleNamespace

        args = SimpleNamespace(refresh=5)

        with patch("otterrip_core.dashboard.DashboardApp") as MockApp:
            mock_instance = MagicMock()
            mock_instance.run.side_effect = KeyboardInterrupt()
            MockApp.return_value = mock_instance

            result = run_dashboard(args)
            assert result == 0

    def test_run_dashboard_exception(self):
        from otterrip_core.dashboard import run_dashboard
        from types import SimpleNamespace

        args = SimpleNamespace(refresh=5)

        with patch("otterrip_core.dashboard.DashboardApp") as MockApp:
            mock_instance = MagicMock()
            mock_instance.run.side_effect = Exception("test error")
            MockApp.return_value = mock_instance

            result = run_dashboard(args)
            assert result == 1


class TestFallbackDashboard:
    """Test fallback when Textual is not installed."""

    def test_fallback_dashboard_app(self):
        """Test that DashboardApp works even without Textual."""
        from otterrip_core.dashboard import DashboardApp, HAS_TEXTUAL

        if not HAS_TEXTUAL:
            # Fallback mode: should have run method
            app = DashboardApp(refresh_interval=1)
            assert hasattr(app, "run")
        else:
            # Textual mode: should have run method too
            app = DashboardApp(refresh_interval=1)
            assert hasattr(app, "run")
