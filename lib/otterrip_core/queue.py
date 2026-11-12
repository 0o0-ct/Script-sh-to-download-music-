#!/usr/bin/env python3
"""Download queue management (queue command)."""
from __future__ import annotations

import json
import os
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


def run_queue(args) -> int:
    """Dispatch queue subcommands."""
    cmd = getattr(args, "queue_cmd", None)

    if cmd == "recover":
        # Mark paused tasks from previous runs as pending
        q = _load_queue()
        changed = False
        for task in q:
            if task.get("status") == "paused" and task.get("pid") != os.getpid():
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
        q = _load_queue()
        for task in q:
            if task["id"] == task_id:
                task["status"] = status
                if error:
                    task["error"] = error
                _save_queue(q)
                return 0
        return 1

    if cmd == "add":
        url = args.url
        dest = getattr(args, "dest", "") or ""
        q = _load_queue()
        new_id = max((t["id"] for t in q), default=0) + 1
        q.append({
            "id": new_id,
            "url": url,
            "dest": dest,
            "status": "pending",
            "created": time.time(),
        })
        _save_queue(q)
        print(new_id)
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
    print("Usage: queue {recover|claim|get|finish|add|list|stats|rm|clear}")
    return 1
