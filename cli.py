#!/usr/bin/env python3
"""
CLI for the persistent browser agent.

    python cli.py start tasks/example_pricing_research.py
    python cli.py resume <task_id>
    python cli.py list
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

from agent.dashboard import run_dashboard
from agent.runner import resume_task, start_task
from memory.store import MemoryStore


def _load_task_module(path: str):
    spec = importlib.util.spec_from_file_location("task_module", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cmd_start(args):
    task_module = _load_task_module(args.task_file)
    store = MemoryStore()
    # Task modules export GOAL and STEPS directly (built via whichever
    # planner function fits their shape — see agent/planner.py) so cli.py
    # doesn't need to know which planner a given task uses.
    cache_ttl = getattr(task_module, "CACHE_TTL_SECONDS", None)
    task_id = start_task(task_module.GOAL, task_module.STEPS, store, cache_ttl_seconds=cache_ttl)
    print(f"\ntask_id: {task_id}")
    print(f"if interrupted, resume with: python cli.py resume {task_id}")


def cmd_resume(args):
    store = MemoryStore()
    resume_task(args.task_id, store)


def cmd_list(args):
    store = MemoryStore()
    tasks = store.list_tasks()
    if not tasks:
        print("no tasks yet — run `python cli.py start <task_file>`")
        return
    for t in tasks:
        print(f"{t['task_id']}  [{t['status']:^11}]  {t['goal']}  (updated {t['updated_at']})")


def cmd_dashboard(args):
    run_dashboard(port=args.port, open_browser=not args.no_browser)


def main():
    parser = argparse.ArgumentParser(description="Persistent browser agent CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_start = sub.add_parser("start", help="start a new task from a task file")
    p_start.add_argument("task_file", help="path to a task definition, e.g. tasks/example_pricing_research.py")
    p_start.set_defaults(func=cmd_start)

    p_resume = sub.add_parser("resume", help="resume an interrupted task by id")
    p_resume.add_argument("task_id")
    p_resume.set_defaults(func=cmd_resume)

    p_list = sub.add_parser("list", help="list all tasks and their status")
    p_list.set_defaults(func=cmd_list)

    p_dash = sub.add_parser("dashboard", help="open a live web dashboard showing task/step progress")
    p_dash.add_argument("--port", type=int, default=8420)
    p_dash.add_argument("--no-browser", action="store_true", help="don't auto-open a browser tab")
    p_dash.set_defaults(func=cmd_dashboard)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    main()