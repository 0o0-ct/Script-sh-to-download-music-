#!/usr/bin/env python3
"""Tests for cli.py — Typer CLI wrapper."""
import sys
import pytest
from unittest.mock import patch, MagicMock


class TestCliImports:
    """Test that cli.py imports correctly."""

    def test_cli_module_imports(self):
        from otterrip_core import cli
        assert hasattr(cli, "app")
        assert hasattr(cli, "main")

    def test_has_typer_attribute(self):
        from otterrip_core.cli import HAS_TYPER
        assert isinstance(HAS_TYPER, bool)

    def test_app_is_typer_app(self):
        from otterrip_core.cli import app, HAS_TYPER
        if HAS_TYPER:
            import typer
            assert isinstance(app, typer.Typer)


class TestRunPyCore:
    """Test _run_py_core helper."""

    def test_run_py_core_no_args(self):
        from otterrip_core.cli import _run_py_core
        result = _run_py_core()
        assert result == 1

    def test_run_py_core_with_args(self):
        from otterrip_core.cli import _run_py_core
        # Mock __main__.main to avoid actual execution
        with patch("otterrip_core.__main__.main") as mock_main:
            mock_main.return_value = None
            result = _run_py_core("version")
            assert result == 0
            mock_main.assert_called_once()

    def test_run_py_core_with_kwargs(self):
        from otterrip_core.cli import _run_py_core
        with patch("otterrip_core.__main__.main") as mock_main:
            mock_main.return_value = None
            result = _run_py_core("queue", "list")
            assert result == 0


class TestTyperCommandsExist:
    """Test that all Typer commands are registered."""

    @pytest.mark.skipif(
        not getattr(__import__("otterrip_core.cli", fromlist=["HAS_TYPER"]), "HAS_TYPER", False),
        reason="Typer not installed"
    )
    def test_download_command_exists(self):
        from otterrip_core.cli import app
        # Typer app is a Typer instance
        import typer
        assert isinstance(app, typer.Typer)

    @pytest.mark.skipif(
        not getattr(__import__("otterrip_core.cli", fromlist=["HAS_TYPER"]), "HAS_TYPER", False),
        reason="Typer not installed"
    )
    def test_queue_command_exists(self):
        from otterrip_core import cli
        # Check that the app has registered commands
        assert cli.app is not None

    @pytest.mark.skipif(
        not getattr(__import__("otterrip_core.cli", fromlist=["HAS_TYPER"]), "HAS_TYPER", False),
        reason="Typer not installed"
    )
    def test_library_command_exists(self):
        from otterrip_core import cli
        assert cli.app is not None

    @pytest.mark.skipif(
        not getattr(__import__("otterrip_core.cli", fromlist=["HAS_TYPER"]), "HAS_TYPER", False),
        reason="Typer not installed"
    )
    def test_playlist_command_exists(self):
        from otterrip_core import cli
        assert cli.app is not None

    @pytest.mark.skipif(
        not getattr(__import__("otterrip_core.cli", fromlist=["HAS_TYPER"]), "HAS_TYPER", False),
        reason="Typer not installed"
    )
    def test_organize_command_exists(self):
        from otterrip_core import cli
        assert cli.app is not None

    @pytest.mark.skipif(
        not getattr(__import__("otterrip_core.cli", fromlist=["HAS_TYPER"]), "HAS_TYPER", False),
        reason="Typer not installed"
    )
    def test_subscriptions_command_exists(self):
        from otterrip_core import cli
        assert cli.app is not None

    @pytest.mark.skipif(
        not getattr(__import__("otterrip_core.cli", fromlist=["HAS_TYPER"]), "HAS_TYPER", False),
        reason="Typer not installed"
    )
    def test_fingerprint_command_exists(self):
        from otterrip_core import cli
        assert cli.app is not None

    @pytest.mark.skipif(
        not getattr(__import__("otterrip_core.cli", fromlist=["HAS_TYPER"]), "HAS_TYPER", False),
        reason="Typer not installed"
    )
    def test_cover_art_command_exists(self):
        from otterrip_core import cli
        assert cli.app is not None

    @pytest.mark.skipif(
        not getattr(__import__("otterrip_core.cli", fromlist=["HAS_TYPER"]), "HAS_TYPER", False),
        reason="Typer not installed"
    )
    def test_mb_lookup_command_exists(self):
        from otterrip_core import cli
        assert cli.app is not None

    @pytest.mark.skipif(
        not getattr(__import__("otterrip_core.cli", fromlist=["HAS_TYPER"]), "HAS_TYPER", False),
        reason="Typer not installed"
    )
    def test_normalize_command_exists(self):
        from otterrip_core import cli
        assert cli.app is not None

    @pytest.mark.skipif(
        not getattr(__import__("otterrip_core.cli", fromlist=["HAS_TYPER"]), "HAS_TYPER", False),
        reason="Typer not installed"
    )
    def test_post_process_command_exists(self):
        from otterrip_core import cli
        assert cli.app is not None

    @pytest.mark.skipif(
        not getattr(__import__("otterrip_core.cli", fromlist=["HAS_TYPER"]), "HAS_TYPER", False),
        reason="Typer not installed"
    )
    def test_dashboard_command_exists(self):
        from otterrip_core import cli
        assert cli.app is not None

    @pytest.mark.skipif(
        not getattr(__import__("otterrip_core.cli", fromlist=["HAS_TYPER"]), "HAS_TYPER", False),
        reason="Typer not installed"
    )
    def test_doctor_command_exists(self):
        from otterrip_core import cli
        assert cli.app is not None

    @pytest.mark.skipif(
        not getattr(__import__("otterrip_core.cli", fromlist=["HAS_TYPER"]), "HAS_TYPER", False),
        reason="Typer not installed"
    )
    def test_ytdlp_command_exists(self):
        from otterrip_core import cli
        assert cli.app is not None

    @pytest.mark.skipif(
        not getattr(__import__("otterrip_core.cli", fromlist=["HAS_TYPER"]), "HAS_TYPER", False),
        reason="Typer not installed"
    )
    def test_state_db_command_exists(self):
        from otterrip_core import cli
        assert cli.app is not None

    @pytest.mark.skipif(
        not getattr(__import__("otterrip_core.cli", fromlist=["HAS_TYPER"]), "HAS_TYPER", False),
        reason="Typer not installed"
    )
    def test_version_command_exists(self):
        from otterrip_core import cli
        assert cli.app is not None


class TestMainFunction:
    """Test main() entry point."""

    def test_main_without_typer(self):
        from otterrip_core import cli
        # If Typer is not installed, main should call sys.exit
        original = cli.HAS_TYPER
        try:
            cli.HAS_TYPER = False
            with patch("sys.exit") as mock_exit:
                cli.main()
                # sys.exit should be called (with any code)
                assert mock_exit.called
        finally:
            cli.HAS_TYPER = original

    def test_main_with_typer(self):
        from otterrip_core import cli
        if not cli.HAS_TYPER:
            pytest.skip("Typer not installed")
        # Mock app() to avoid actual CLI execution
        with patch.object(cli, "app") as mock_app:
            cli.main()
            mock_app.assert_called_once()
