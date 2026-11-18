#!/usr/bin/env python3
"""Unit tests for otterrip_core.queue module (SQLite backend)."""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

# Ensure lib is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from otterrip_core import queue as queue_mod
from otterrip_core.state_db import StateDB


class TestQueue(unittest.TestCase):
    """Test queue management subcommands via StateDB."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.orig_state = queue_mod._state_dir
        queue_mod._state_dir = self.tmpdir
        # Create a fresh StateDB so tables exist
        self.db = StateDB(self.tmpdir)

    def tearDown(self):
        queue_mod._state_dir = self.orig_state
        self.db.close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _clear_queue(self):
        """Reset queue to empty."""
        self.db.queue_clear()

    # -- add --

    def test_add_single_url(self):
        self._clear_queue()
        args = SimpleNamespace(queue_cmd="add", urls=["https://example.com/1"], dest="")
        rc = queue_mod.run_queue(args)
        self.assertEqual(rc, 0)
        q = self.db.queue_list()
        self.assertEqual(len(q), 1)
        self.assertEqual(q[0]["url"], "https://example.com/1")
        self.assertEqual(q[0]["status"], "pending")

    def test_add_multiple_urls(self):
        self._clear_queue()
        urls = [
            "https://example.com/1",
            "https://example.com/2",
            "https://example.com/3",
        ]
        args = SimpleNamespace(queue_cmd="add", urls=urls, dest="")
        rc = queue_mod.run_queue(args)
        self.assertEqual(rc, 0)
        q = self.db.queue_list()
        self.assertEqual(len(q), 3)
        for task in q:
            self.assertEqual(task["status"], "pending")

    def test_add_with_dest(self):
        self._clear_queue()
        args = SimpleNamespace(queue_cmd="add", urls=["https://example.com/1"], dest="/my/dest")
        rc = queue_mod.run_queue(args)
        self.assertEqual(rc, 0)
        q = self.db.queue_list()
        self.assertEqual(q[0]["dest"], "/my/dest")

    # -- claim --

    def test_claim_pending(self):
        self._clear_queue()
        self.db.queue_add("https://example.com/1")
        args = SimpleNamespace(queue_cmd="claim", pid=12345)
        rc = queue_mod.run_queue(args)
        self.assertEqual(rc, 0)
        q = self.db.queue_list()
        self.assertEqual(q[0]["status"], "running")
        self.assertEqual(q[0]["pid"], 12345)

    def test_claim_none_available(self):
        self._clear_queue()
        args = SimpleNamespace(queue_cmd="claim", pid=12345)
        rc = queue_mod.run_queue(args)
        self.assertEqual(rc, 1)

    # -- finish --

    def test_finish_task(self):
        self._clear_queue()
        self.db.queue_add("https://example.com/1")
        self.db.queue_claim(pid=12345)
        args = SimpleNamespace(
            queue_cmd="finish", task_id="1", status="done", error="", summary="",
        )
        rc = queue_mod.run_queue(args)
        self.assertEqual(rc, 0)
        q = self.db.queue_list()
        self.assertEqual(q[0]["status"], "done")

    def test_finish_with_error(self):
        self._clear_queue()
        self.db.queue_add("https://example.com/1")
        self.db.queue_claim(pid=12345)
        args = SimpleNamespace(
            queue_cmd="finish", task_id="1", status="failed", error="network error", summary="",
        )
        rc = queue_mod.run_queue(args)
        self.assertEqual(rc, 0)
        q = self.db.queue_list()
        self.assertEqual(q[0]["status"], "failed")
        self.assertEqual(q[0]["error"], "network error")

    def test_finish_with_summary(self):
        self._clear_queue()
        self.db.queue_add("https://example.com/1")
        self.db.queue_claim(pid=12345)
        summary = '{"ok":2,"errors":0,"verify_failed":0,"class":"single"}'
        args = SimpleNamespace(
            queue_cmd="finish", task_id="1", status="done", error="", summary=summary,
        )
        rc = queue_mod.run_queue(args)
        self.assertEqual(rc, 0)
        q = self.db.queue_list()
        self.assertEqual(q[0]["summary"], summary)

    # -- pause --

    def test_pause_task(self):
        self._clear_queue()
        self.db.queue_add("https://example.com/1")
        self.db.queue_add("https://example.com/2")
        args = SimpleNamespace(queue_cmd="pause", ids=["1"])
        rc = queue_mod.run_queue(args)
        self.assertEqual(rc, 0)
        q = self.db.queue_list()
        self.assertEqual(q[0]["status"], "paused")
        self.assertEqual(q[1]["status"], "pending")

    def test_pause_all(self):
        self._clear_queue()
        self.db.queue_add("https://example.com/1")
        self.db.queue_add("https://example.com/2")
        args = SimpleNamespace(queue_cmd="pause", ids=["all"])
        rc = queue_mod.run_queue(args)
        self.assertEqual(rc, 0)
        q = self.db.queue_list()
        self.assertEqual(q[0]["status"], "paused")
        self.assertEqual(q[1]["status"], "paused")

    # -- resume --

    def test_resume_task(self):
        self._clear_queue()
        self.db.queue_add("https://example.com/1")
        self.db.queue_add("https://example.com/2")
        self.db.queue_pause([1, 2])
        args = SimpleNamespace(queue_cmd="resume", ids=["1"])
        rc = queue_mod.run_queue(args)
        self.assertEqual(rc, 0)
        q = self.db.queue_list()
        self.assertEqual(q[0]["status"], "pending")
        self.assertEqual(q[1]["status"], "paused")

    def test_resume_all(self):
        self._clear_queue()
        self.db.queue_add("https://example.com/1")
        self.db.queue_add("https://example.com/2")
        self.db.queue_pause([1, 2])
        args = SimpleNamespace(queue_cmd="resume", ids=["all"])
        rc = queue_mod.run_queue(args)
        self.assertEqual(rc, 0)
        q = self.db.queue_list()
        self.assertEqual(q[0]["status"], "pending")
        self.assertEqual(q[1]["status"], "pending")

    # -- cancel --

    def test_cancel_task(self):
        self._clear_queue()
        self.db.queue_add("https://example.com/1")
        args = SimpleNamespace(queue_cmd="cancel", ids=["1"])
        rc = queue_mod.run_queue(args)
        self.assertEqual(rc, 0)
        q = self.db.queue_list()
        self.assertEqual(q[0]["status"], "cancelled")

    # -- retry --

    def test_retry_failed(self):
        self._clear_queue()
        self.db.queue_add("https://example.com/1")
        self.db.queue_finish(1, "failed", error="err")
        args = SimpleNamespace(queue_cmd="retry", ids=["1"])
        rc = queue_mod.run_queue(args)
        self.assertEqual(rc, 0)
        q = self.db.queue_list()
        self.assertEqual(q[0]["status"], "pending")

    def test_retry_all(self):
        self._clear_queue()
        self.db.queue_add("https://example.com/1")
        self.db.queue_add("https://example.com/2")
        self.db.queue_finish(1, "failed", error="err")
        self.db.queue_finish(2, "partial")
        args = SimpleNamespace(queue_cmd="retry", ids=["all"])
        rc = queue_mod.run_queue(args)
        self.assertEqual(rc, 0)
        q = self.db.queue_list()
        for task in q:
            self.assertEqual(task["status"], "pending")

    # -- clear-done --

    def test_clear_done(self):
        self._clear_queue()
        self.db.queue_add("https://example.com/1")
        self.db.queue_add("https://example.com/2")
        self.db.queue_add("https://example.com/3")
        self.db.queue_finish(1, "done")
        self.db.queue_finish(2, "failed")
        args = SimpleNamespace(queue_cmd="clear-done")
        rc = queue_mod.run_queue(args)
        self.assertEqual(rc, 0)
        q = self.db.queue_list()
        self.assertEqual(len(q), 1)
        self.assertEqual(q[0]["status"], "pending")

    # -- recover --

    def test_recover_running_dead_pid(self):
        self._clear_queue()
        self.db.queue_add("https://example.com/1")
        # Manually set running with dead PID
        self.db._conn.execute(
            "UPDATE queue SET status = 'running', pid = 99999 WHERE id = 1"
        )
        self.db._conn.commit()
        args = SimpleNamespace(queue_cmd="recover")
        rc = queue_mod.run_queue(args)
        self.assertEqual(rc, 0)
        q = self.db.queue_list()
        self.assertEqual(q[0]["status"], "pending")

    # -- list and stats --

    def test_list_empty(self):
        self._clear_queue()
        args = SimpleNamespace(queue_cmd="list")
        rc = queue_mod.run_queue(args)
        self.assertEqual(rc, 0)

    def test_stats(self):
        self._clear_queue()
        self.db.queue_add("https://example.com/1")
        self.db.queue_add("https://example.com/2")
        self.db.queue_claim(pid=12345)
        args = SimpleNamespace(queue_cmd="stats")
        rc = queue_mod.run_queue(args)
        self.assertEqual(rc, 0)

    # -- rm --

    def test_rm(self):
        self._clear_queue()
        self.db.queue_add("https://example.com/1")
        args = SimpleNamespace(queue_cmd="rm", task_id="1")
        rc = queue_mod.run_queue(args)
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.db.queue_list()), 0)

    # -- clear --

    def test_clear(self):
        self._clear_queue()
        self.db.queue_add("https://example.com/1")
        self.db.queue_add("https://example.com/2")
        args = SimpleNamespace(queue_cmd="clear")
        rc = queue_mod.run_queue(args)
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.db.queue_list()), 0)

    # -- renew-lease --

    def test_renew_lease(self):
        self._clear_queue()
        self.db.queue_add("https://example.com/1")
        self.db.queue_claim(pid=12345)
        args = SimpleNamespace(queue_cmd="renew-lease", task_id="1", lease_seconds=300)
        rc = queue_mod.run_queue(args)
        self.assertEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
