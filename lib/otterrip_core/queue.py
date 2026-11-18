#!/usr/bin/env python3
"""Download queue management (queue command)."""
from __future__ import annotations

import json
import os
import signal
import time
from pathlib import Path

# Queue stored as JSON in OTTERRIP_STATE_DIR
_state_dir = os.environ.get("OTTERRIP_STATE_DIR", "")
_queue_file = Path(_state_dir) / "queue.json" if _state_dir else None


def _load_queue() -> list[dict]:
    if _queue_file and _queue_file.is_file():
        try:
            return json.loads(_queue_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
    return []


def _save_queue(q: list[dict]) -> None:
    if _queue_file:
        _queue_file.parent.mkdir(parents=True, exist_ok=True)
        _queue_file.write_text(json.dumps(q, indent=2, ensure_ascii=False), encoding="utf-8")


def _is_pid_alive(pid: int) -> bool:
    """Check if a process with the given PID is still running."""
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but we don't have permission to signal it
        return True
    except OSError:
        return False


def _resolve_ids(q: list[dict], raw_ids: list[str]) -> list[int]:
    """Convert a list of id strings to integers, resolving 'all' to all task ids."""
    if len(raw_ids) == 1 and raw_ids[0].lower() == "all":
        return [t["id"] for t in q]
    return [int(x) for x in raw_ids]


def run_queue(args) -> int:
    """Dispatch queue subcommands."""
    cmd = getattr(args, "queue_cmd", None)

    if cmd == "recover":
        # Mark paused tasks from previous runs as pending
        # Also detect running tasks with dead PIDs
        q = _load_queue()
        changed = False
        for task in q:
            status = task.get("status")
            if status == "paused" and task.get("pid") != os.getpid():
                task["status"] = "pending"
                changed = True
            elif status == "running":
                pid = task.get("pid")
                if pid and not _is_pid_alive(pid):
                    task["status"] = "pending"
                    changed = True
        if changed:
            _save_queue(q)
        return 0

    if cmd == "claim":
        pid = args.pid
        q = _load_queue()
        for task in q:
            if task.get("status") == "pending":
                task["status"] = "running"
                task["pid"] = pid
                _save_queue(q)
                print(task["id"])
                return 0
        return 1  # no tasks available

    if cmd == "get":
        task_id = int(args.task_id)
        field = args.field
        q = _load_queue()
        for task in q:
            if task["id"] == task_id:
                val = task.get(field, "")
                if val:
                    print(val)
                return 0
        return 1

    if cmd == "finish":
        task_id = int(args.task_id)
        status = args.status
        error = getattr(args, "error", "")
        summary = getattr(args, "summary", "") or ""
        q = _load_queue()
        for task in q:
            if task["id"] == task_id:
                task["status"] = status
                if error:
                    task["error"] = error
                if summary:
                    task["summary"] = summary
                _save_queue(q)
                return 0
        return 1

    if cmd == "add":
        urls = getattr(args, "urls", None) or [getattr(args, "url", "")]
        dest = getattr(args, "dest", "") or ""
        q = _load_queue()
        new_ids = []
        for url in urls:
            if not url:
                continue
            new_id = max((t["id"] for t in q), default=0) + 1
            q.append({
                "id": new_id,
                "url": url,
                "dest": dest,
                "status": "pending",
                "created": time.time(),
            })
            new_ids.append(new_id)
        _save_queue(q)
        for nid in new_ids:
            print(nid)
        return 0

    if cmd == "pause":
        raw_ids = getattr(args, "ids", [])
        q = _load_queue()
        target_ids = _resolve_ids(q, raw_ids)
        paused = 0
        for task in q:
            if task["id"] in target_ids and task.get("status") == "pending":
                task["status"] = "paused"
                paused += 1
        _save_queue(q)
        print(paused)
        return 0

    if cmd == "resume":
        raw_ids = getattr(args, "ids", [])
        q = _load_queue()
        target_ids = _resolve_ids(q, raw_ids)
        resumed = 0
        for task in q:
            if task["id"] in target_ids and task.get("status") == "paused":
                task["status"] = "pending"
                resumed += 1
        _save_queue(q)
        print(resumed)
        return 0

    if cmd == "cancel":
        raw_ids = getattr(args, "ids", [])
        q = _load_queue()
        target_ids = _resolve_ids(q, raw_ids)
        cancelled = 0
        for task in q:
            if task["id"] in target_ids:
                old = task.get("status")
                if old in ("pending", "paused", "running"):
                    task["status"] = "cancelled"
                    cancelled += 1
        _save_queue(q)
        print(cancelled)
        return 0

    if cmd == "retry":
        raw_ids = getattr(args, "ids", [])
        q = _load_queue()
        target_ids = _resolve_ids(q, raw_ids)
        retried = 0
        for task in q:
            if task["id"] in target_ids:
                old = task.get("status")
                if old in ("failed", "partial", "cancelled"):
                    task["status"] = "pending"
                    task.pop("error", None)
                    retried += 1
        _save_queue(q)
        print(retried)
        return 0

    if cmd == "clear-done":
        done_statuses = {"done", "failed", "cancelled"}
        q = _load_queue()
        new_q = [t for t in q if t.get("status") not in done_statuses]
        count = len(q) - len(new_q)
        _save_queue(new_q)
        print(count)
        return 0

    if cmd == "list":
        q = _load_queue()
        if not q:
            print("  (cola vacía)")
            return 0
        for task in q:
            tid = task["id"]
            status = task.get("status", "?")
            url = task.get("url", "?")[:80]
            print(f"  #{tid} [{status}] {url}")
        return 0

    if cmd == "stats":
        q = _load_queue()
        counts = {"pending": 0, "running": 0, "done": 0, "paused": 0, "failed": 0}
        for task in q:
            s = task.get("status", "pending")
            counts[s] = counts.get(s, 0) + 1
        print(f"total={len(q)}")
        for k, v in counts.items():
            print(f"{k}={v}")
        return 0

    if cmd == "rm":
        task_id = int(args.task_id)
        q = _load_queue()
        new_q = [t for t in q if t["id"] != task_id]
        if len(new_q) < len(q):
            _save_queue(new_q)
            print(f"Eliminada tarea #{task_id}")
        else:
            print(f"Tarea #{task_id} no encontrada")
        return 0

    if cmd == "clear":
        q = _load_queue()
        count = len(q)
        _save_queue([])
        print(count)
        return 0

    # No subcommand
    print("Usage: queue {recover|claim|get|finish|add|list|stats|rm|clear|pause|resume|cancel|retry|clear-done}")
    return 1
