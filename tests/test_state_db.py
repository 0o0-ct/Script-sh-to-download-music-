#!/usr/bin/env python3
"""Unit tests for otterrip_core.state_db module (SQLite backend)."""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

# Ensure lib is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from otterrip_core import state_db


class TestStateDBQueue(unittest.TestCase):
    """Test queue table operations via StateDB."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db = state_db.StateDB(self.tmpdir)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # -- add --

    def test_add_single(self):
        tid = self.db.queue_add("https://example.com/1")
        self.assertEqual(tid, 1)
        tasks = self.db.queue_list()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["url"], "https://example.com/1")
        self.assertEqual(tasks[0]["status"], "pending")

    def test_add_multiple(self):
        ids = [self.db.queue_add(f"https://example.com/{i}") for i in range(1, 4)]
        self.assertEqual(ids, [1, 2, 3])
        self.assertEqual(len(self.db.queue_list()), 3)

    def test_add_with_dest(self):
        self.db.queue_add("https://example.com/1", dest="/my/music")
        task = self.db.queue_list()[0]
        self.assertEqual(task["dest"], "/my/music")

    # -- claim --

    def test_claim_pending(self):
        self.db.queue_add("https://example.com/1")
        task = self.db.queue_claim(pid=12345)
        self.assertIsNotNone(task)
        self.assertEqual(task["id"], 1)
        self.assertEqual(task["status"], "running")
        self.assertEqual(task["pid"], 12345)
        self.assertIsNotNone(task["lease_expires_at"])

    def test_claim_none_available(self):
        self.assertIsNone(self.db.queue_claim(pid=12345))

    def test_claim_oldest_first(self):
        self.db.queue_add("https://example.com/1")
        self.db.queue_add("https://example.com/2")
        task = self.db.queue_claim(pid=12345)
        self.assertEqual(task["id"], 1)

    # -- finish --

    def test_finish_task(self):
        self.db.queue_add("https://example.com/1")
        self.db.queue_claim(pid=12345)
        self.assertTrue(self.db.queue_finish(1, "done"))
        task = self.db.queue_list()[0]
        self.assertEqual(task["status"], "done")

    def test_finish_with_error(self):
        self.db.queue_add("https://example.com/1")
        self.db.queue_claim(pid=12345)
        self.db.queue_finish(1, "failed", error="network error")
        task = self.db.queue_list()[0]
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["error"], "network error")

    def test_finish_with_summary(self):
        self.db.queue_add("https://example.com/1")
        self.db.queue_claim(pid=12345)
        summary = '{"ok":1,"errors":0}'
        self.db.queue_finish(1, "done", summary=summary)
        task = self.db.queue_list()[0]
        self.assertEqual(task["summary"], summary)

    def test_finish_nonexistent(self):
        self.assertFalse(self.db.queue_finish(999, "done"))

    # -- recover --

    def test_recover_expired_lease(self):
        self.db.queue_add("https://example.com/1")
        self.db.queue_claim(pid=99999, lease_seconds=300)
        # Manually expire the lease
        self.db._conn.execute(
            "UPDATE queue SET lease_expires_at = ? WHERE id = 1",
            (time.time() - 1000,),
        )
        self.db._conn.commit()
        recovered = self.db.queue_recover()
        self.assertGreater(recovered, 0)
        task = self.db.queue_list()[0]
        self.assertEqual(task["status"], "pending")

    def test_recover_dead_pid(self):
        self.db.queue_add("https://example.com/1")
        self.db.queue_claim(pid=99999, lease_seconds=300)
        # PID 99999 is unlikely to exist
        recovered = self.db.queue_recover()
        self.assertGreater(recovered, 0)
        task = self.db.queue_list()[0]
        self.assertEqual(task["status"], "pending")

    # -- pause --

    def test_pause_task(self):
        self.db.queue_add("https://example.com/1")
        self.db.queue_add("https://example.com/2")
        count = self.db.queue_pause([1])
        self.assertEqual(count, 1)
        tasks = self.db.queue_list()
        self.assertEqual(tasks[0]["status"], "paused")
        self.assertEqual(tasks[1]["status"], "pending")

    # -- resume --

    def test_resume_task(self):
        self.db.queue_add("https://example.com/1")
        self.db.queue_pause([1])
        count = self.db.queue_resume([1])
        self.assertEqual(count, 1)
        task = self.db.queue_list()[0]
        self.assertEqual(task["status"], "pending")

    # -- cancel --

    def test_cancel_task(self):
        self.db.queue_add("https://example.com/1")
        count = self.db.queue_cancel([1])
        self.assertEqual(count, 1)
        task = self.db.queue_list()[0]
        self.assertEqual(task["status"], "cancelled")

    # -- retry --

    def test_retry_failed(self):
        self.db.queue_add("https://example.com/1")
        self.db.queue_finish(1, "failed", error="err")
        count = self.db.queue_retry([1])
        self.assertEqual(count, 1)
        task = self.db.queue_list()[0]
        self.assertEqual(task["status"], "pending")
        self.assertIsNone(task["error"])

    def test_retry_non_retriable(self):
        self.db.queue_add("https://example.com/1")
        count = self.db.queue_retry([1])
        self.assertEqual(count, 0)

    # -- clear-done --

    def test_clear_done(self):
        self.db.queue_add("https://example.com/1")
        self.db.queue_add("https://example.com/2")
        self.db.queue_finish(1, "done")
        self.db.queue_finish(2, "failed")
        count = self.db.queue_clear_done()
        self.assertEqual(count, 2)
        self.assertEqual(len(self.db.queue_list()), 0)

    def test_clear_done_keeps_pending(self):
        self.db.queue_add("https://example.com/1")
        self.db.queue_add("https://example.com/2")
        self.db.queue_finish(1, "done")
        count = self.db.queue_clear_done()
        self.assertEqual(count, 1)
        tasks = self.db.queue_list()
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["status"], "pending")

    # -- list & stats --

    def test_list_empty(self):
        self.assertEqual(self.db.queue_list(), [])

    def test_stats(self):
        self.db.queue_add("https://example.com/1")
        self.db.queue_add("https://example.com/2")
        self.db.queue_claim(pid=12345)
        stats = self.db.queue_stats()
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["pending"], 1)
        self.assertEqual(stats["running"], 1)

    # -- get --

    def test_get_field(self):
        self.db.queue_add("https://example.com/1")
        val = self.db.queue_get(1, "url")
        self.assertEqual(val, "https://example.com/1")

    def test_get_nonexistent(self):
        with self.assertRaises(KeyError):
            self.db.queue_get(999, "url")

    # -- rm --

    def test_rm(self):
        self.db.queue_add("https://example.com/1")
        self.assertTrue(self.db.queue_rm(1))
        self.assertEqual(len(self.db.queue_list()), 0)

    def test_rm_nonexistent(self):
        self.assertFalse(self.db.queue_rm(999))

    # -- clear --

    def test_clear(self):
        self.db.queue_add("https://example.com/1")
        self.db.queue_add("https://example.com/2")
        count = self.db.queue_clear()
        self.assertEqual(count, 2)
        self.assertEqual(len(self.db.queue_list()), 0)

    # -- renew_lease --

    def test_renew_lease(self):
        self.db.queue_add("https://example.com/1")
        self.db.queue_claim(pid=12345)
        self.assertTrue(self.db.queue_renew_lease(1))

    def test_renew_lease_nonexistent(self):
        self.assertFalse(self.db.queue_renew_lease(999))


class TestStateDBHistory(unittest.TestCase):
    """Test history table operations via StateDB."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db = state_db.StateDB(self.tmpdir)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_append(self):
        rid = self.db.history_append("url", "title", "/dest", "ok", 120)
        self.assertEqual(rid, 1)
        self.assertEqual(self.db.history_count(), 1)

    def test_list(self):
        self.db.history_append("url1", "title1", "/dest", "ok", 60)
        self.db.history_append("url2", "title2", "/dest", "failed", 30)
        entries = self.db.history_list()
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["url"], "url1")

    def test_list_max_rows(self):
        for i in range(5):
            self.db.history_append(f"url{i}", f"title{i}", "/dest", "ok", 10)
        entries = self.db.history_list(max_rows=3)
        self.assertEqual(len(entries), 3)

    def test_clear_all(self):
        self.db.history_append("url1", "t1", "/d", "ok", 10)
        self.db.history_append("url2", "t2", "/d", "failed", 10)
        count = self.db.history_clear("all")
        self.assertEqual(count, 2)
        self.assertEqual(self.db.history_count(), 0)

    def test_clear_failed(self):
        self.db.history_append("url1", "t1", "/d", "ok", 10)
        self.db.history_append("url2", "t2", "/d", "failed", 10)
        count = self.db.history_clear("failed")
        self.assertEqual(count, 1)
        self.assertEqual(self.db.history_count(), 1)

    def test_clear_partial(self):
        self.db.history_append("url1", "t1", "/d", "ok", 10)
        self.db.history_append("url2", "t2", "/d", "partial", 10)
        count = self.db.history_clear("partial")
        self.assertEqual(count, 1)

    def test_get_dests(self):
        self.db.history_append("url1", "t1", "/dest1", "ok", 10)
        self.db.history_append("url2", "t2", "/dest2", "ok", 10)
        self.db.history_append("url3", "t3", "/dest1", "ok", 10)
        dests = self.db.history_get_dests()
        self.assertEqual(set(dests), {"/dest1", "/dest2"})


class TestStateDBLyricsCache(unittest.TestCase):
    """Test lyrics_cache table operations via StateDB."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db = state_db.StateDB(self.tmpdir)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_set_and_get(self):
        bundle = {"synced": "[00:00] hello", "plain": "hello"}
        self.db.lyrics_cache_set("abc123", bundle, keyed_by="legacy")
        result = self.db.lyrics_cache_get("abc123")
        self.assertEqual(result["synced"], "[00:00] hello")

    def test_get_missing(self):
        self.assertIsNone(self.db.lyrics_cache_get("nonexistent"))

    def test_ttl_expiration(self):
        bundle = {"synced": "[00:00] hello"}
        self.db.lyrics_cache_set("abc123", bundle)
        # Manually expire
        self.db._conn.execute(
            "UPDATE lyrics_cache SET cached_at = ? WHERE key = ?",
            (time.time() - 700000, "abc123"),
        )
        self.db._conn.commit()
        self.assertIsNone(self.db.lyrics_cache_get("abc123", ttl_seconds=604800))

    def test_clear(self):
        self.db.lyrics_cache_set("key1", {"synced": "a"})
        self.db.lyrics_cache_set("key2", {"synced": "b"})
        count = self.db.lyrics_cache_clear()
        self.assertEqual(count, 2)
        self.assertIsNone(self.db.lyrics_cache_get("key1"))


class TestStateDBMetadataCache(unittest.TestCase):
    """Test metadata_cache table operations via StateDB."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db = state_db.StateDB(self.tmpdir)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_set_and_get(self):
        data = {"artist": "Test Artist", "album": "Test Album"}
        self.db.metadata_cache_set("artist:test", data)
        result = self.db.metadata_cache_get("artist:test")
        self.assertEqual(result["artist"], "Test Artist")

    def test_get_missing(self):
        self.assertIsNone(self.db.metadata_cache_get("nonexistent"))

    def test_clear(self):
        self.db.metadata_cache_set("key1", {"a": 1})
        self.db.metadata_cache_set("key2", {"b": 2})
        count = self.db.metadata_cache_clear()
        self.assertEqual(count, 2)


class TestStateDBMigration(unittest.TestCase):
    """Test automatic migration from legacy formats."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_migrate_queue_json(self):
        qfile = Path(self.tmpdir) / "queue.json"
        qfile.write_text(
            json.dumps([
                {"id": 1, "url": "https://example.com/1", "dest": "/d1", "status": "pending", "created": 1000},
                {"id": 2, "url": "https://example.com/2", "dest": "", "status": "running", "pid": 123, "created": 2000},
            ]),
            encoding="utf-8",
        )
        with state_db.StateDB(self.tmpdir) as db:
            tasks = db.queue_list()
            self.assertEqual(len(tasks), 2)
            self.assertEqual(tasks[0]["url"], "https://example.com/1")
            self.assertEqual(tasks[1]["status"], "running")
        # Backup should exist
        self.assertTrue((Path(self.tmpdir) / "queue.json.bak").exists())

    def test_migrate_history_tsv(self):
        data_dir = Path(self.tmpdir) / "data"
        data_dir.mkdir()
        os.environ["OTTERRIP_DATA_DIR"] = str(data_dir)
        tsv = data_dir / "history.tsv"
        tsv.write_text(
            "2024-01-01 12:00:00\thttps://example.com/1\tSong 1\t/d1\tok\t120\n"
            "2024-01-02 13:00:00\thttps://example.com/2\tSong 2\t/d2\tfailed\t60\n",
            encoding="utf-8",
        )
        with state_db.StateDB(self.tmpdir) as db:
            entries = db.history_list()
            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[0]["url"], "https://example.com/1")
            self.assertEqual(entries[1]["status"], "failed")
        self.assertTrue((tsv.with_suffix(".tsv.bak")).exists())
        del os.environ["OTTERRIP_DATA_DIR"]

    def test_migrate_lyrics_cache_files(self):
        cache_dir = Path(self.tmpdir) / "lyrics_cache"
        cache_dir.mkdir()
        os.environ["OTTERRIP_CACHE_DIR"] = self.tmpdir
        entry = cache_dir / "abc123.json"
        entry.write_text(
            json.dumps({
                "cached_at": time.time(),
                "bundle": {"synced": "[00:00] test"},
                "keyed_by": "legacy",
            }),
            encoding="utf-8",
        )
        with state_db.StateDB(self.tmpdir) as db:
            result = db.lyrics_cache_get("abc123")
            self.assertIsNotNone(result)
            self.assertEqual(result["synced"], "[00:00] test")
        # Original file should be deleted
        self.assertFalse(entry.exists())
        del os.environ["OTTERRIP_CACHE_DIR"]


class TestStateDBWAL(unittest.TestCase):
    """Test WAL mode is enabled."""

    def test_wal_mode(self):
        tmpdir = tempfile.mkdtemp()
        try:
            db = state_db.StateDB(tmpdir)
            # PRAGMA returns a dict with row_factory; use the column name
            row = db._conn.execute("PRAGMA journal_mode").fetchone()
            mode = row.get("journal_mode", row.get(None, "")) if isinstance(row, dict) else row[0]
            self.assertEqual(mode, "wal")
            db.close()
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
