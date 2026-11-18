#!/usr/bin/env python3
"""Unit tests for otterrip_core.queue module."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

# Ensure lib is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from otterrip_core import queue as queue_mod


class TestQueue(unittest.TestCase):
    """Test queue management subcommands."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.orig_state = queue_mod._state_dir
        self.orig_file = queue_mod._queue_file
        queue_mod._state_dir = self.tmpdir
        queue_mod._queue_file = Path(self.tmpdir) / "queue.json"

    def tearDown(self):
        queue_mod._state_dir = self.orig_state
        queue_mod._queue_file = self.orig_file
        # Clean up queue file if it exists
        qf = Path(self.tmpdir) / "queue.json"
        if qf.exists():
            qf.unlink()

    def _clear_queue(self):
        """Reset queue to empty."""
        qf = Path(self.tmpdir) / "queue.json"
        if qf.exists():
            qf.unlink()

    # -- add --

    def test_add_single_url(self):
        self._clear_queue()
        args = SimpleNamespace(queue_cmd="add", urls=["https://example.com/1"], dest="")
        rc = queue_mod.run_queue(args)
        self.assertEqual(rc, 0)
        q = queue_mod._load_queue()
        self.assertEqual(len(q), 1)
        self.assertEqual(q[0]["id"], 1)
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
        q = queue_mod._load_queue()
        self.assertEqual(len(q), 3)
        self.assertEqual(q[0]["id"], 1)
        self.assertEqual(q[1]["id"], 2)
        self.assertEqual(q[2]["id"], 3)
        for task in q:
            self.assertEqual(task["status"], "pending")

    def test_add_with_dest(self):
        self._clear_queue()
        args = SimpleNamespace(queue_cmd="add", urls=["https://example.com/1"], dest="/my/dest")
        rc = queue_mod.run_queue(args)
        self.assertEqual(rc, 0)
        q = queue_mod._load_queue()
        self.assertEqual(q[0]["dest"], "/my/dest")

    # -- claim --

    def test_claim_pending(self):
        self._clear_queue()
        # Add a pending task
        queue_mod._save_queue([{
            "id": 1, "url": "https://example.com/1", "dest": "",
            "status": "pending", "created": 0,
        }])
        args = SimpleNamespace(queue_cmd="claim", pid=12345)
        rc = queue_mod.run_queue(args)
        self.assertEqual(rc, 0)
        q = queue_mod._load_queue()
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
        queue_mod._save_queue([{
            "id": 1, "url": "https://example.com/1", "dest": "",
            "status": "running", "pid": 12345, "created": 0,
        }])
        args = SimpleNamespace(
            queue_cmd="finish", task_id="1", status="done", error="", summary="",
        )
        rc = queue_mod.run_queue(args)
        self.assertEqual(rc, 0)
        q = queue_mod._load_queue()
        self.assertEqual(q[0]["status"], "done")

    def test_finish_with_error(self):
        self._clear_queue()
        queue_mod._save_queue([{
            "id": 1, "url": "https://example.com/1", "dest": "",
            "status": "running", "pid": 12345, "created": 0,
        }])
        args = SimpleNamespace(
            queue_cmd="finish", task_id="1", status="failed", error="network error", summary="",
        )
        rc = queue_mod.run_queue(args)
        self.assertEqual(rc, 0)
        q = queue_mod._load_queue()
        self.assertEqual(q[0]["status"], "failed")
        self.assertEqual(q[0]["error"], "network error")

    def test_finish_with_summary(self):
        self._clear_queue()
        queue_mod._save_queue([{
            "id": 1, "url": "https://example.com/1", "dest": "",
            "status": "running", "pid": 12345, "created": 0,
        }])
        summary = '{"ok":2,"errors":0,"verify_failed":0,"class":"single"}'
        args = SimpleNamespace(
            queue_cmd="finish", task_id="1", status="done", error="", summary=summary,
        )
        rc = queue_mod.run_queue(args)
        self.assertEqual(rc, 0)
        q = queue_mod._load_queue()
        self.assertEqual(q[0]["summary"], summary)

    # -- pause --

    def test_pause_task(self):
        self._clear_queue()
        queue_mod._save_queue([
            {"id": 1, "url": "https://example.com/1", "dest": "", "status": "pending", "created": 0},
            {"id": 2, "url": "https://example.com/2", "dest": "", "status": "pending", "created": 0},
        ])
        args = SimpleNamespace(queue_cmd="pause", ids=["1"])
        rc = queue_mod.run_queue(args)
        self.assertEqual(rc, 0)
        q = queue_mod._load_queue()
        self.assertEqual(q[0]["status"], "paused")
        self.assertEqual(q[1]["status"], "pending")

    def test_pause_all(self):
        self._clear_queue()
        queue_mod._save_queue([
            {"id": 1, "url": "https://example.com/1", "dest": "", "status": "pending", "created": 0},
            {"id": 2, "url": "https://example.com/2", "dest": "", "status": "pending", "created": 0},
        ])
        args = SimpleNamespace(queue_cmd="pause", ids=["all"])
        rc = queue_mod.run_queue(args)
        self.assertEqual(rc, 0)
        q = queue_mod._load_queue()
        self.assertEqual(q[0]["status"], "paused")
        self.assertEqual(q[1]["status"], "paused")

    # -- resume --

    def test_resume_task(self):
        self._clear_queue()
        queue_mod._save_queue([
            {"id": 1, "url": "https://example.com/1", "dest": "", "status": "paused", "created": 0},
            {"id": 2, "url": "https://example.com/2", "dest": "", "status": "paused", "created": 0},
        ])
        args = SimpleNamespace(queue_cmd="resume", ids=["1"])
        rc = queue_mod.run_queue(args)
        self.assertEqual(rc, 0)
        q = queue_mod._load_queue()
        self.assertEqual(q[0]["status"], "pending")
        self.assertEqual(q[1]["status"], "paused")

    def test_resume_all(self):
        self._clear_queue()
        queue_mod._save_queue([
            {"id": 1, "url": "https://example.com/1", "dest": "", "status": "paused", "created": 0},
            {"id": 2, "url": "https://example.com/2", "dest": "", "status": "paused", "created": 0},
        ])
        args = SimpleNamespace(queue_cmd="resume", ids=["all"])
        rc = queue_mod.run_queue(args)
        self.assertEqual(rc, 0)
        q = queue_mod._load_queue()
        self.assertEqual(q[0]["status"], "pending")
        self.assertEqual(q[1]["status"], "pending")

    # -- cancel --

    def test_cancel_task(self):
        self._clear_queue()
        queue_mod._save_queue([
            {"id": 1, "url": "https://example.com/1", "dest": "", "status": "pending", "created": 0},
            {"id": 2, "url": "https://example.com/2", "dest": "", "status": "running", "pid": 999, "created": 0},
        ])
        args = SimpleNamespace(queue_cmd="cancel", ids=["1"])
        rc = queue_mod.run_queue(args)
        self.assertEqual(rc, 0)
        q = queue_mod._load_queue()
        self.assertEqual(q[0]["status"], "cancelled")
        self.assertEqual(q[1]["status"], "running")

    # -- retry --

    def test_retry_failed(self):
        self._clear_queue()
        queue_mod._save_queue([
            {"id": 1, "url": "https://example.com/1", "dest": "", "status": "failed", "error": "err", "created": 0},
            {"id": 2, "url": "https://example.com/2", "dest": "", "status": "done", "created": 0},
        ])
        args = SimpleNamespace(queue_cmd="retry", ids=["1"])
        rc = queue_mod.run_queue(args)
        self.assertEqual(rc, 0)
        q = queue_mod._load_queue()
        self.assertEqual(q[0]["status"], "pending")
        self.assertNotIn("error", q[0])
        self.assertEqual(q[1]["status"], "done")

    def test_retry_all(self):
        self._clear_queue()
        queue_mod._save_queue([
            {"id": 1, "url": "https://example.com/1", "dest": "", "status": "failed", "error": "e1", "created": 0},
            {"id": 2, "url": "https://example.com/2", "dest": "", "status": "partial", "created": 0},
            {"id": 3, "url": "https://example.com/3", "dest": "", "status": "cancelled", "created": 0},
            {"id": 4, "url": "https://example.com/4", "dest": "", "status": "done", "created": 0},
        ])
        args = SimpleNamespace(queue_cmd="retry", ids=["all"])
        rc = queue_mod.run_queue(args)
        self.assertEqual(rc, 0)
        q = queue_mod._load_queue()
        self.assertEqual(q[0]["status"], "pending")
        self.assertEqual(q[1]["status"], "pending")
        self.assertEqual(q[2]["status"], "pending")
        self.assertEqual(q[3]["status"], "done")

    # -- clear-done --

    def test_clear_done(self):
        self._clear_queue()
        queue_mod._save_queue([
            {"id": 1, "url": "https://example.com/1", "dest": "", "status": "done", "created": 0},
            {"id": 2, "url": "https://example.com/2", "dest": "", "status": "pending", "created": 0},
            {"id": 3, "url": "https://example.com/3", "dest": "", "status": "failed", "created": 0},
            {"id": 4, "url": "https://example.com/4", "dest": "", "status": "cancelled", "created": 0},
        ])
        args = SimpleNamespace(queue_cmd="clear-done")
        rc = queue_mod.run_queue(args)
        self.assertEqual(rc, 0)
        q = queue_mod._load_queue()
        self.assertEqual(len(q), 1)
        self.assertEqual(q[0]["id"], 2)
        self.assertEqual(q[0]["status"], "pending")

    # -- recover --

    def test_recover_running_dead_pid(self):
        self._clear_queue()
        # Use a PID that definitely doesn't exist
        dead_pid = 99999999
        queue_mod._save_queue([
            {"id": 1, "url": "https://example.com/1", "dest": "", "status": "running", "pid": dead_pid, "created": 0},
            {"id": 2, "url": "https://example.com/2", "dest": "", "status": "pending", "created": 0},
        ])
        args = SimpleNamespace(queue_cmd="recover")
        rc = queue_mod.run_queue(args)
        self.assertEqual(rc, 0)
        q = queue_mod._load_queue()
        self.assertEqual(q[0]["status"], "pending")
        self.assertEqual(q[1]["status"], "pending")

    def test_recover_running_alive_pid(self):
        self._clear_queue()
        # Current process PID is alive
        alive_pid = os.getpid()
        queue_mod._save_queue([
            {"id": 1, "url": "https://example.com/1", "dest": "", "status": "running", "pid": alive_pid, "created": 0},
        ])
        args = SimpleNamespace(queue_cmd="recover")
        rc = queue_mod.run_queue(args)
        self.assertEqual(rc, 0)
        q = queue_mod._load_queue()
        # Should stay running since PID is alive
        self.assertEqual(q[0]["status"], "running")

    # -- list and stats --

    def test_list_empty(self):
        self._clear_queue()
        args = SimpleNamespace(queue_cmd="list")
        with patch("sys.stdout", new_callable=__import__("io").StringIO) as mock_stdout:
            rc = queue_mod.run_queue(args)
            self.assertEqual(rc, 0)
            self.assertIn("cola vacía", mock_stdout.getvalue())

    def test_list_with_tasks(self):
        self._clear_queue()
        queue_mod._save_queue([
            {"id": 1, "url": "https://example.com/1", "dest": "", "status": "pending", "created": 0},
        ])
        args = SimpleNamespace(queue_cmd="list")
        with patch("sys.stdout", new_callable=__import__("io").StringIO) as mock_stdout:
            rc = queue_mod.run_queue(args)
            self.assertEqual(rc, 0)
            output = mock_stdout.getvalue()
            self.assertIn("#1", output)
            self.assertIn("pending", output)

    def test_stats(self):
        self._clear_queue()
        queue_mod._save_queue([
            {"id": 1, "url": "https://example.com/1", "dest": "", "status": "pending", "created": 0},
            {"id": 2, "url": "https://example.com/2", "dest": "", "status": "running", "pid": 1, "created": 0},
            {"id": 3, "url": "https://example.com/3", "dest": "", "status": "done", "created": 0},
        ])
        args = SimpleNamespace(queue_cmd="stats")
        with patch("sys.stdout", new_callable=__import__("io").StringIO) as mock_stdout:
            rc = queue_mod.run_queue(args)
            self.assertEqual(rc, 0)
            output = mock_stdout.getvalue()
            self.assertIn("total=3", output)
            self.assertIn("pending=1", output)
            self.assertIn("running=1", output)
            self.assertIn("done=1", output)


class TestIsPidAlive(unittest.TestCase):
    """Test the _is_pid_alive helper."""

    def test_current_pid_alive(self):
        self.assertTrue(queue_mod._is_pid_alive(os.getpid()))

    def test_nonexistent_pid(self):
        self.assertFalse(queue_mod._is_pid_alive(99999999))

    def test_none_pid(self):
        self.assertFalse(queue_mod._is_pid_alive(None))

    def test_zero_pid(self):
        self.assertFalse(queue_mod._is_pid_alive(0))


class TestResolveIds(unittest.TestCase):
    """Test the _resolve_ids helper."""

    def test_resolve_specific_ids(self):
        q = [{"id": 1}, {"id": 2}, {"id": 3}]
        result = queue_mod._resolve_ids(q, ["1", "3"])
        self.assertEqual(result, [1, 3])

    def test_resolve_all(self):
        q = [{"id": 1}, {"id": 2}, {"id": 3}]
        result = queue_mod._resolve_ids(q, ["all"])
        self.assertEqual(result, [1, 2, 3])

    def test_resolve_all_case_insensitive(self):
        q = [{"id": 1}, {"id": 2}]
        result = queue_mod._resolve_ids(q, ["ALL"])
        self.assertEqual(result, [1, 2])


if __name__ == "__main__":
    unittest.main()
