#!/usr/bin/env python3
"""OtterRip State Database — single SQLite backend for all state.

Replaces JSON/TSV file-based storage with a single SQLite database
using WAL mode for concurrent read/write safety.

Tables:
    queue         — download tasks with lease-based recovery
    history       — completed/partial/failed download records
    lyrics_cache  — cached lyric bundles keyed by track signature
    metadata_cache — cached artist/album/cover metadata
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import sqlite3
import time
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# PID helpers
# ---------------------------------------------------------------------------

def _is_pid_alive(pid: int) -> bool:
    """Check whether a process with *pid* is still running."""
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Row factory
# ---------------------------------------------------------------------------

def _dict_factory(cursor, row):
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d


# ---------------------------------------------------------------------------
# StateDB
# ---------------------------------------------------------------------------

class StateDB:
    """Central SQLite state manager for OtterRip.

    Creates ``state.db`` in *state_dir* with WAL mode and four tables.
    On first init it attempts to migrate any legacy data (queue.json,
    history.tsv, lyrics_cache/*.json) and backs up the originals.
    """

    LEASE_SECONDS: int = 300  # default lease window

    def __init__(self, state_dir: str | Path):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        db_path = self.state_dir / "state.db"
        self._conn: sqlite3.Connection = sqlite3.connect(str(db_path))
        self._conn.row_factory = _dict_factory
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._create_tables()
        self._migrate_legacy()

    # -- connection management -----------------------------------------------

    def commit(self) -> None:
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- schema --------------------------------------------------------------

    def _create_tables(self) -> None:
        # Use individual execute() calls instead of executescript()
        # because executescript() may reset row_factory in some Python versions.
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                dest TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                error TEXT,
                summary TEXT,
                pid INTEGER,
                lease_expires_at REAL,
                created REAL NOT NULL,
                updated REAL
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                url TEXT NOT NULL,
                title TEXT NOT NULL,
                dest TEXT NOT NULL,
                status TEXT NOT NULL,
                duration INTEGER NOT NULL DEFAULT 0
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS lyrics_cache (
                key TEXT PRIMARY KEY,
                bundle TEXT NOT NULL,
                keyed_by TEXT NOT NULL DEFAULT 'legacy',
                cached_at REAL NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS metadata_cache (
                key TEXT PRIMARY KEY,
                data TEXT NOT NULL,
                cached_at REAL NOT NULL
            )
        """)
        self._conn.commit()

    # -- legacy migration ----------------------------------------------------

    def _migrate_legacy(self) -> None:
        """Import data from legacy JSON/TSV files and back them up."""
        self._migrate_queue_json()
        self._migrate_history_tsv()
        self._migrate_lyrics_cache_files()

    def _migrate_queue_json(self) -> None:
        qfile = self.state_dir / "queue.json"
        if not qfile.is_file():
            return
        try:
            data = json.loads(qfile.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        if not isinstance(data, list) or not data:
            return
        count = 0
        for task in data:
            url = task.get("url", "")
            if not url:
                continue
            self._conn.execute(
                """INSERT INTO queue (url, dest, status, error, summary, pid,
                                       lease_expires_at, created, updated)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    url,
                    task.get("dest", ""),
                    task.get("status", "pending"),
                    task.get("error"),
                    task.get("summary"),
                    task.get("pid"),
                    task.get("lease_expires_at"),
                    task.get("created", time.time()),
                    task.get("updated"),
                ),
            )
            count += 1
        if count:
            bak = qfile.with_suffix(".json.bak")
            try:
                shutil.copy2(str(qfile), str(bak))
            except OSError:
                pass
            self._conn.commit()

    def _migrate_history_tsv(self) -> None:
        tsv = Path(os.environ.get("OTTERRIP_DATA_DIR", "")) / "history.tsv"
        if not tsv.is_file():
            return
        try:
            lines = tsv.read_text(encoding="utf-8").strip().splitlines()
        except OSError:
            return
        count = 0
        for line in lines:
            parts = line.split("\t")
            if len(parts) < 5:
                continue
            ts, url, title, dest, status = parts[0], parts[1], parts[2], parts[3], parts[4]
            duration = int(parts[5]) if len(parts) > 5 and parts[5].isdigit() else 0
            self._conn.execute(
                """INSERT INTO history (timestamp, url, title, dest, status, duration)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (ts, url, title, dest, status, duration),
            )
            count += 1
        if count:
            bak = tsv.with_suffix(".tsv.bak")
            try:
                shutil.copy2(str(tsv), str(bak))
            except OSError:
                pass
            self._conn.commit()

    def _migrate_lyrics_cache_files(self) -> None:
        cache_dir = Path(os.environ.get("OTTERRIP_CACHE_DIR", "")) / "lyrics_cache"
        if not cache_dir.is_dir():
            return
        count = 0
        for p in cache_dir.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            key = p.stem
            bundle = data.get("bundle")
            if bundle is None:
                continue
            self._conn.execute(
                """INSERT OR REPLACE INTO lyrics_cache (key, bundle, keyed_by, cached_at)
                   VALUES (?, ?, ?, ?)""",
                (
                    key,
                    json.dumps(bundle, ensure_ascii=False),
                    data.get("keyed_by", "legacy"),
                    data.get("cached_at", time.time()),
                ),
            )
            try:
                p.unlink()
            except OSError:
                pass
            count += 1
        if count:
            self._conn.commit()

    # =====================================================================
    # Queue methods
    # =====================================================================

    def queue_add(self, url: str, dest: str = "") -> int:
        """Insert a new pending task. Returns the new task id."""
        cur = self._conn.execute(
            """INSERT INTO queue (url, dest, status, created) VALUES (?, ?, 'pending', ?)""",
            (url, dest, time.time()),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def queue_claim(self, pid: int, lease_seconds: int = 300) -> Optional[dict]:
        """Claim the first pending task, setting a lease. Returns task dict or None."""
        row = self._conn.execute(
            "SELECT * FROM queue WHERE status = 'pending' ORDER BY id ASC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        task_id = row["id"]
        expires = time.time() + lease_seconds
        self._conn.execute(
            """UPDATE queue SET status = 'running', pid = ?, lease_expires_at = ?, updated = ?
               WHERE id = ?""",
            (pid, expires, time.time(), task_id),
        )
        self._conn.commit()
        # Re-fetch to return the updated row
        updated = self._conn.execute(
            "SELECT * FROM queue WHERE id = ?", (task_id,)
        ).fetchone()
        return dict(updated) if updated else dict(row)

    def queue_finish(self, task_id: int, status: str, error: str = "", summary: str = "") -> bool:
        """Mark a task as finished. Returns True if the task existed."""
        cur = self._conn.execute(
            """UPDATE queue SET status = ?, error = ?, summary = ?, updated = ?
               WHERE id = ?""",
            (status, error or None, summary or None, time.time(), task_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def queue_recover(self) -> int:
        """Recover orphaned tasks (expired leases or dead PIDs). Returns count recovered."""
        now = time.time()
        # Expired leases
        cur1 = self._conn.execute(
            """UPDATE queue SET status = 'pending', pid = NULL, lease_expires_at = NULL,
                                updated = ?
               WHERE status = 'running' AND lease_expires_at IS NOT NULL AND lease_expires_at < ?""",
            (now, now),
        )
        self._conn.commit()
        recovered = cur1.rowcount

        # Dead PIDs (for tasks without expired lease check)
        running = self._conn.execute(
            "SELECT * FROM queue WHERE status = 'running'"
        ).fetchall()
        for task in running:
            pid = task.get("pid")
            if pid and not _is_pid_alive(pid):
                self._conn.execute(
                    """UPDATE queue SET status = 'pending', pid = NULL,
                                       lease_expires_at = NULL, updated = ?
                       WHERE id = ?""",
                    (time.time(), task["id"]),
                )
                recovered += 1
        if recovered:
            self._conn.commit()
        return recovered

    def queue_pause(self, ids: list[int]) -> int:
        """Pause pending tasks. Returns count paused."""
        placeholders = ",".join("?" for _ in ids)
        cur = self._conn.execute(
            f"UPDATE queue SET status = 'paused', updated = ? WHERE id IN ({placeholders}) AND status = 'pending'",
            [time.time()] + ids,
        )
        self._conn.commit()
        return cur.rowcount

    def queue_resume(self, ids: list[int]) -> int:
        """Resume paused tasks. Returns count resumed."""
        placeholders = ",".join("?" for _ in ids)
        cur = self._conn.execute(
            f"UPDATE queue SET status = 'pending', updated = ? WHERE id IN ({placeholders}) AND status = 'paused'",
            [time.time()] + ids,
        )
        self._conn.commit()
        return cur.rowcount

    def queue_cancel(self, ids: list[int]) -> int:
        """Cancel tasks. Returns count cancelled."""
        placeholders = ",".join("?" for _ in ids)
        cur = self._conn.execute(
            f"UPDATE queue SET status = 'cancelled', updated = ? WHERE id IN ({placeholders})",
            [time.time()] + ids,
        )
        self._conn.commit()
        return cur.rowcount

    def queue_retry(self, ids: list[int]) -> int:
        """Retry failed/partial/cancelled tasks. Returns count retried."""
        placeholders = ",".join("?" for _ in ids)
        cur = self._conn.execute(
            f"UPDATE queue SET status = 'pending', error = NULL, updated = ? "
            f"WHERE id IN ({placeholders}) AND status IN ('failed', 'partial', 'cancelled')",
            [time.time()] + ids,
        )
        self._conn.commit()
        return cur.rowcount

    def queue_clear_done(self) -> int:
        """Remove tasks with terminal status. Returns count removed."""
        cur = self._conn.execute(
            "DELETE FROM queue WHERE status IN ('done', 'failed', 'cancelled')"
        )
        self._conn.commit()
        return cur.rowcount

    def queue_list(self) -> list[dict]:
        """Return all queue tasks ordered by id."""
        rows = self._conn.execute(
            "SELECT * FROM queue ORDER BY id ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    def queue_stats(self) -> dict:
        """Return counts grouped by status."""
        rows = self._conn.execute(
            "SELECT status, COUNT(*) as cnt FROM queue GROUP BY status"
        ).fetchall()
        stats = {r["status"]: r["cnt"] for r in rows}
        stats["total"] = sum(stats.values())
        return stats

    def queue_get(self, task_id: int, field: str) -> str:
        """Return a single field value for a task. Raises KeyError if not found."""
        row = self._conn.execute(
            "SELECT * FROM queue WHERE id = ?", (task_id,)
        ).fetchone()
        if row is None:
            raise KeyError(f"Task {task_id} not found")
        val = row.get(field, "")
        return str(val) if val else ""

    def queue_rm(self, task_id: int) -> bool:
        """Remove a single task by id. Returns True if it existed."""
        cur = self._conn.execute("DELETE FROM queue WHERE id = ?", (task_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def queue_clear(self) -> int:
        """Remove all tasks. Returns count removed."""
        cur = self._conn.execute("DELETE FROM queue")
        self._conn.commit()
        return cur.rowcount

    def queue_renew_lease(self, task_id: int, lease_seconds: int = 300) -> bool:
        """Renew the lease for a running task. Returns True if renewed."""
        expires = time.time() + lease_seconds
        cur = self._conn.execute(
            """UPDATE queue SET lease_expires_at = ?, updated = ?
               WHERE id = ? AND status = 'running'""",
            (expires, time.time(), task_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    # =====================================================================
    # History methods
    # =====================================================================

    def history_append(self, url: str, title: str, dest: str, status: str, duration: int = 0) -> int:
        """Add a history entry. Returns the new row id."""
        cur = self._conn.execute(
            """INSERT INTO history (timestamp, url, title, dest, status, duration)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (time.strftime("%Y-%m-%d %H:%M:%S"), url, title, dest, status, duration),
        )
        self._conn.commit()
        return cur.lastrowid  # type: ignore[return-value]

    def history_list(self, max_rows: int = 50) -> list[dict]:
        """Return the most recent history entries."""
        rows = self._conn.execute(
            "SELECT * FROM history ORDER BY id DESC LIMIT ?", (max_rows,)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def history_clear(self, mode: str = "all") -> int:
        """Clear history entries. Mode: 'all', 'failed', 'partial'. Returns count removed."""
        if mode == "all":
            cur = self._conn.execute("DELETE FROM history")
        elif mode == "failed":
            cur = self._conn.execute("DELETE FROM history WHERE status = 'failed'")
        elif mode == "partial":
            cur = self._conn.execute("DELETE FROM history WHERE status = 'partial'")
        else:
            return 0
        self._conn.commit()
        return cur.rowcount

    def history_count(self) -> int:
        """Return total number of history entries."""
        row = self._conn.execute("SELECT COUNT(*) as cnt FROM history").fetchone()
        return row["cnt"] if row else 0

    def history_get_dests(self) -> list[str]:
        """Return unique destination paths from history."""
        rows = self._conn.execute(
            "SELECT DISTINCT dest FROM history WHERE dest != '' ORDER BY dest"
        ).fetchall()
        return [r["dest"] for r in rows]

    # =====================================================================
    # Lyrics cache methods
    # =====================================================================

    def lyrics_cache_get(self, key: str, ttl_seconds: int = 604800) -> Optional[dict]:
        """Get a cached lyric bundle. Returns None if missing or expired."""
        now = time.time()
        row = self._conn.execute(
            "SELECT * FROM lyrics_cache WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        cached_at = row["cached_at"]
        if (now - cached_at) > ttl_seconds:
            self._conn.execute("DELETE FROM lyrics_cache WHERE key = ?", (key,))
            self._conn.commit()
            return None
        return json.loads(row["bundle"])

    def lyrics_cache_set(self, key: str, bundle: dict, keyed_by: str = "legacy") -> None:
        """Store a lyric bundle in the cache."""
        self._conn.execute(
            """INSERT OR REPLACE INTO lyrics_cache (key, bundle, keyed_by, cached_at)
               VALUES (?, ?, ?, ?)""",
            (key, json.dumps(bundle, ensure_ascii=False), keyed_by, time.time()),
        )
        self._conn.commit()

    def lyrics_cache_clear(self) -> int:
        """Remove all cached lyrics. Returns count removed."""
        cur = self._conn.execute("DELETE FROM lyrics_cache")
        self._conn.commit()
        return cur.rowcount

    # =====================================================================
    # Metadata cache methods
    # =====================================================================

    def metadata_cache_get(self, key: str) -> Optional[dict]:
        """Get cached metadata. Returns None if missing."""
        row = self._conn.execute(
            "SELECT * FROM metadata_cache WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        return json.loads(row["data"])

    def metadata_cache_set(self, key: str, data: dict, ttl_seconds: int = 604800) -> None:
        """Store metadata in the cache."""
        self._conn.execute(
            """INSERT OR REPLACE INTO metadata_cache (key, data, cached_at)
               VALUES (?, ?, ?)""",
            (key, json.dumps(data, ensure_ascii=False), time.time()),
        )
        self._conn.commit()

    def metadata_cache_clear(self) -> int:
        """Remove all cached metadata. Returns count removed."""
        cur = self._conn.execute("DELETE FROM metadata_cache")
        self._conn.commit()
        return cur.rowcount
