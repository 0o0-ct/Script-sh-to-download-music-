#!/usr/bin/env python3
"""Download queue management (queue command).

Delegates all operations to StateDB (SQLite backend) while maintaining
the same CLI API for backward compatibility.
"""

from __future__ import annotations

import os

from otterrip_core.state_db import StateDB

# StateDB lives in OTTERRIP_STATE_DIR
_state_dir = os.environ.get("OTTERRIP_STATE_DIR", "")


def _get_db() -> StateDB:
    """Return a StateDB instance, creating the directory if needed."""
    if not _state_dir:
        raise RuntimeError("OTTERRIP_STATE_DIR not set")
    return StateDB(_state_dir)


def _resolve_ids(raw_ids: list[str], db: StateDB) -> list[int]:
    """Convert a list of id strings to integers, resolving 'all' to all task ids."""
    if len(raw_ids) == 1 and raw_ids[0].lower() == "all":
        return [t["id"] for t in db.queue_list()]
    return [int(x) for x in raw_ids]


def run_queue(args) -> int:
    """Dispatch queue subcommands via StateDB."""
    cmd = getattr(args, "queue_cmd", None)
    db = _get_db()

    if cmd == "recover":
        recovered = db.queue_recover()
        if recovered:
            print(f"Recuperadas: {recovered}")
        return 0

    if cmd == "claim":
        pid = args.pid
        task = db.queue_claim(pid=pid)
        if task is not None:
            print(task["id"])
            return 0
        return 1  # no tasks available

    if cmd == "get":
        task_id = int(args.task_id)
        field = args.field
        try:
            val = db.queue_get(task_id, field)
            if val:
                print(val)
            return 0
        except KeyError:
            return 1

    if cmd == "finish":
        task_id = int(args.task_id)
        status = args.status
        error = getattr(args, "error", "") or ""
        summary = getattr(args, "summary", "") or ""
        if db.queue_finish(task_id, status, error, summary):
            return 0
        return 1

    if cmd == "add":
        urls = getattr(args, "urls", None) or [getattr(args, "url", "")]
        dest = getattr(args, "dest", "") or ""
        new_ids = []
        for url in urls:
            if not url:
                continue
            new_id = db.queue_add(url, dest)
            new_ids.append(new_id)
        for nid in new_ids:
            print(nid)
        return 0

    if cmd == "pause":
        raw_ids = getattr(args, "ids", [])
        target_ids = _resolve_ids(raw_ids, db)
        paused = db.queue_pause(target_ids)
        print(paused)
        return 0

    if cmd == "resume":
        raw_ids = getattr(args, "ids", [])
        target_ids = _resolve_ids(raw_ids, db)
        resumed = db.queue_resume(target_ids)
        print(resumed)
        return 0

    if cmd == "cancel":
        raw_ids = getattr(args, "ids", [])
        target_ids = _resolve_ids(raw_ids, db)
        cancelled = db.queue_cancel(target_ids)
        print(cancelled)
        return 0

    if cmd == "retry":
        raw_ids = getattr(args, "ids", [])
        target_ids = _resolve_ids(raw_ids, db)
        retried = db.queue_retry(target_ids)
        print(retried)
        return 0

    if cmd == "clear-done":
        count = db.queue_clear_done()
        print(count)
        return 0

    if cmd == "list":
        tasks = db.queue_list()
        if not tasks:
            print("  (cola vacía)")
            return 0
        for task in tasks:
            tid = task["id"]
            status = task.get("status", "?")
            url = task.get("url", "?")[:80]
            print(f"  #{tid} [{status}] {url}")
        return 0

    if cmd == "stats":
        stats = db.queue_stats()
        print(f"total={stats['total']}")
        for k, v in stats.items():
            if k != "total":
                print(f"{k}={v}")
        return 0

    if cmd == "rm":
        task_id = int(args.task_id)
        if db.queue_rm(task_id):
            print(f"Eliminada tarea #{task_id}")
        else:
            print(f"Tarea #{task_id} no encontrada")
        return 0

    if cmd == "clear":
        count = db.queue_clear()
        print(count)
        return 0

    if cmd == "renew-lease":
        task_id = int(args.task_id)
        lease_seconds = getattr(args, "lease_seconds", 300) or 300
        if db.queue_renew_lease(task_id, lease_seconds):
            return 0
        return 1

    # No subcommand
    print(
        "Usage: queue {recover|claim|get|finish|add|list|stats|rm|clear|pause|resume|cancel|retry|clear-done|renew-lease}"
    )
    return 1
