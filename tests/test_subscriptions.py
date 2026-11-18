#!/usr/bin/env python3
"""Tests for subscriptions.py and state_db.py subscriptions table."""
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from otterrip_core import state_db


class TestStateDBSubscriptions:
    """Test the subscriptions table in StateDB."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db = state_db.StateDB(self.tmpdir)

    def teardown_method(self):
        self.db.close()

    def test_subscription_add(self):
        sub_id = self.db.subscription_add("https://youtube.com/playlist?list=test")
        assert sub_id >= 1

    def test_subscription_add_with_interval(self):
        sub_id = self.db.subscription_add(
            "https://youtube.com/playlist?list=test",
            interval=3600,
            enabled=True,
        )
        assert sub_id >= 1
        subs = self.db.subscription_list()
        assert len(subs) == 1
        assert subs[0]["interval"] == 3600
        assert subs[0]["enabled"] is True

    def test_subscription_add_duplicate_url(self):
        """Adding the same URL twice should fail (UNIQUE constraint)."""
        self.db.subscription_add("https://youtube.com/playlist?list=test")
        with pytest.raises(Exception):
            self.db.subscription_add("https://youtube.com/playlist?list=test")

    def test_subscription_list_empty(self):
        result = self.db.subscription_list()
        assert result == []

    def test_subscription_list_after_add(self):
        self.db.subscription_add("https://youtube.com/playlist?list=test1")
        self.db.subscription_add("https://youtube.com/playlist?list=test2")
        result = self.db.subscription_list()
        assert len(result) == 2

    def test_subscription_remove(self):
        self.db.subscription_add("https://youtube.com/playlist?list=test")
        result = self.db.subscription_remove("https://youtube.com/playlist?list=test")
        assert result is True
        assert len(self.db.subscription_list()) == 0

    def test_subscription_remove_nonexistent(self):
        result = self.db.subscription_remove("https://youtube.com/nonexistent")
        assert result is False

    def test_subscription_update_check(self):
        self.db.subscription_add("https://youtube.com/playlist?list=test")
        result = self.db.subscription_update_check("https://youtube.com/playlist?list=test")
        assert result is True

    def test_subscription_update_check_nonexistent(self):
        result = self.db.subscription_update_check("https://youtube.com/nonexistent")
        assert result is False

    def test_subscription_toggle(self):
        self.db.subscription_add("https://youtube.com/playlist?list=test", enabled=True)
        result = self.db.subscription_toggle("https://youtube.com/playlist?list=test", False)
        assert result is True
        subs = self.db.subscription_list()
        assert subs[0]["enabled"] is False

    def test_subscription_enabled_is_bool(self):
        """Ensure enabled field is returned as bool, not int."""
        self.db.subscription_add("https://youtube.com/playlist?list=test", enabled=True)
        subs = self.db.subscription_list()
        assert isinstance(subs[0]["enabled"], bool)


class TestSubscriptionManager:
    """Test SubscriptionManager class."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        os.environ["OTTERRIP_STATE_DIR"] = self.tmpdir

        from otterrip_core.subscriptions import SubscriptionManager
        self.manager = SubscriptionManager(self.tmpdir)

    def teardown_method(self):
        self.manager.db.close()
        if "OTTERRIP_STATE_DIR" in os.environ:
            del os.environ["OTTERRIP_STATE_DIR"]

    def test_add(self):
        sub_id = self.manager.add("https://youtube.com/playlist?list=test")
        assert sub_id >= 1

    def test_remove(self):
        self.manager.add("https://youtube.com/playlist?list=test")
        result = self.manager.remove("https://youtube.com/playlist?list=test")
        assert result is True

    def test_list_empty(self):
        result = self.manager.list_subscriptions()
        assert result == []

    def test_list_after_add(self):
        self.manager.add("https://youtube.com/playlist?list=test")
        result = self.manager.list_subscriptions()
        assert len(result) == 1

    def test_check_no_subscriptions(self):
        result = self.manager.check()
        assert result["checked"] == []
        assert result["new_songs"] == []
        assert result["errors"] == []


class TestRunSubscriptions:
    """Test CLI handler for subscriptions."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        os.environ["OTTERRIP_STATE_DIR"] = self.tmpdir

    def teardown_method(self):
        if "OTTERRIP_STATE_DIR" in os.environ:
            del os.environ["OTTERRIP_STATE_DIR"]

    def test_no_command(self):
        from otterrip_core.subscriptions import run_subscriptions
        args = MagicMock()
        args.subscriptions_cmd = None
        result = run_subscriptions(args)
        assert result == 1

    def test_add_no_url(self):
        from otterrip_core.subscriptions import run_subscriptions
        args = MagicMock()
        args.subscriptions_cmd = "add"
        args.url = ""
        result = run_subscriptions(args)
        assert result == 1

    def test_remove_no_url(self):
        from otterrip_core.subscriptions import run_subscriptions
        args = MagicMock()
        args.subscriptions_cmd = "remove"
        args.url = ""
        result = run_subscriptions(args)
        assert result == 1

    def test_list_empty(self):
        from otterrip_core.subscriptions import run_subscriptions
        args = MagicMock()
        args.subscriptions_cmd = "list"
        args.json = False
        result = run_subscriptions(args)
        assert result == 0
