"""Queue operations tests with StateDB backend.

Tests verify that queue operations (claim, finish, pause, resume, recover,
clear-done) work correctly and maintain data integrity.
"""
import tempfile

import pytest


def _make_db(state_dir):
    """Create a StateDB instance pointing to a temporary directory."""
    from otterrip_core.state_db import StateDB
    return StateDB(state_dir)


class TestQueueOperations:
    """Test queue operations with StateDB."""

    def setup_method(self):
        self.state_dir = tempfile.mkdtemp()
        self.db = _make_db(self.state_dir)

    def test_claim_unique_tasks(self):
        """10 workers claim 10 tasks; each gets a unique task."""
        for i in range(10):
            self.db.queue_add(f"https://example.com/{i}")

        claimed = []
        for i in range(10):
            result = self.db.queue_claim(pid=1000 + i)
            if result is not None:
                claimed.append(result)

        assert len(claimed) == 10
        ids = [c["id"] for c in claimed]
        assert len(set(ids)) == 10

    def test_finish_tasks(self):
        """5 tasks are claimed and finished; all marked as done."""
        task_ids = []
        for i in range(5):
            tid = self.db.queue_add(f"https://example.com/{i}")
            task_ids.append(tid)

        for i, tid in enumerate(task_ids):
            self.db.queue_claim(pid=2000 + i)
            self.db.queue_finish(task_id=tid, status="done")

        for tid in task_ids:
            task = self.db.queue_get(tid, "status")
            assert task == "done"

    def test_claim_race_condition(self):
        """Multiple claim attempts on 1 task; only 1 succeeds."""
        self.db.queue_add("https://example.com/only-one")

        claimed = 0
        for i in range(20):
            result = self.db.queue_claim(pid=3000 + i)
            if result is not None:
                claimed += 1

        assert claimed == 1

    def test_add_and_claim(self):
        """Add tasks and claim them."""
        for i in range(5):
            self.db.queue_add(f"https://example.com/add-{i}")

        claimed = 0
        for i in range(5):
            result = self.db.queue_claim(pid=4000 + i)
            if result is not None:
                claimed += 1

        assert claimed == 5

    def test_pause_resume(self):
        """Pause and resume tasks."""
        task_ids = []
        for i in range(5):
            tid = self.db.queue_add(f"https://example.com/{i}")
            task_ids.append(tid)

        # Pause all
        for tid in task_ids:
            self.db.queue_pause([tid])

        for tid in task_ids:
            task = self.db.queue_get(tid, "status")
            assert task == "paused"

        # Resume all
        for tid in task_ids:
            self.db.queue_resume([tid])

        for tid in task_ids:
            task = self.db.queue_get(tid, "status")
            assert task == "pending"

    def test_stats_consistency(self):
        """Stats are consistent after operations."""
        task_ids = []
        for i in range(10):
            tid = self.db.queue_add(f"https://example.com/{i}")
            task_ids.append(tid)

        # Claim 5, finish 3 of those 5
        for i, tid in enumerate(task_ids[:5]):
            self.db.queue_claim(pid=5000 + i)
            if i < 3:
                self.db.queue_finish(task_id=tid, status="done")

        stats = self.db.queue_stats()
        assert stats.get("done", 0) == 3
        assert stats.get("running", 0) == 2
        assert stats.get("pending", 0) == 5

    def test_recover_expired_lease(self):
        """Recover detects tasks with expired leases."""
        tid = self.db.queue_add("https://example.com/crash-test")
        self.db.queue_claim(pid=99999999, lease_seconds=0)
        recovered = self.db.queue_recover()
        assert recovered >= 1
        task = self.db.queue_get(tid, "status")
        assert task == "pending"

    def test_recover_dead_pid(self):
        """Recover detects tasks with dead PIDs."""
        tid = self.db.queue_add("https://example.com/dead-pid")
        self.db.queue_claim(pid=99999999, lease_seconds=999999)
        # PID 99999999 doesn't exist, so recover should find it
        recovered = self.db.queue_recover()
        assert recovered >= 1

    def test_clear_done(self):
        """Clear-done removes only terminal tasks."""
        task_ids = []
        for i in range(10):
            tid = self.db.queue_add(f"https://example.com/{i}")
            task_ids.append(tid)

        # Finish 5 tasks
        for i, tid in enumerate(task_ids[:5]):
            self.db.queue_claim(pid=6000 + i)
            self.db.queue_finish(task_id=tid, status="done")

        removed = self.db.queue_clear_done()
        assert removed == 5

        remaining = self.db.queue_list()
        assert len(remaining) == 5
