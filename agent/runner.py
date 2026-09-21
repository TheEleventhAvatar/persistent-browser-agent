"""
Orchestrates a task: creates/loads it from the MemoryStore, walks the steps,
runs each one in the browser, checkpoints after every step, and periodically
compresses the scratchpad. This is where "resume" actually happens.

Two additions beyond basic checkpointing:

  - Cross-task memory (facts cache): before re-scraping a URL, check
    whether a recent-enough result for that exact URL is already sitting in
    the `facts` table from ANY prior task run. If so, reuse it instead of
    spinning up a browser — this is what makes memory persist BETWEEN
    separate task runs, not just within one task's resume.
  - Error-adaptive retry: when a step is retried (resume, or a fresh call
    after a prior failure), the error from the previous attempt is passed
    into run_step so the model's first decision on the retry is informed by
    what went wrong last time, instead of blindly repeating it.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlparse

from . import llm
from .browser_agent import run_step
from memory.store import MemoryStore

SCRATCHPAD_COMPRESS_EVERY_N_STEPS = 5
DEFAULT_CACHE_TTL_SECONDS = 1800  # 30 minutes, used when a task doesn't set its own


def start_task(
    goal: str,
    step_descriptions: list[str],
    store: MemoryStore,
    cache_ttl_seconds: int | None = None,
) -> str:
    task = store.create_task(goal, step_descriptions, cache_ttl_seconds=cache_ttl_seconds)
    print(f"[task {task.task_id}] created with {len(task.steps)} sub-goals")
    run_task(task.task_id, store)
    return task.task_id


def resume_task(task_id: str, store: MemoryStore):
    task = store.load_task(task_id)
    print(f"[task {task_id}] resuming: {store.progress_summary(task_id)}")
    next_step = store.next_pending_step(task_id)
    if next_step is None:
        print(f"[task {task_id}] nothing left to do — already complete")
        return
    print(f"[task {task_id}] continuing from: {next_step.description}")
    run_task(task_id, store)


def _extract_target_url(description: str) -> str:
    """sub-goal descriptions from planner.py embed the URL as 'Visit <url> and ...'."""
    return description.split(" and ", 1)[0].replace("Visit ", "").strip()


def _normalize_url(url: str) -> str:
    """Used as the fact cache key so trivial variations (trailing slash,
    scheme redirects) still hit the same cached fact."""
    parsed = urlparse(url)
    return f"{parsed.netloc}{parsed.path}".rstrip("/").lower()


def run_task(task_id: str, store: MemoryStore):
    store.set_task_status(task_id, "in_progress")
    completed_since_compress = 0

    while True:
        step = store.next_pending_step(task_id)
        if step is None:
            break

        print(f"[task {task_id}] step {step.step_index}: {step.description}")
        store.mark_step_in_progress(step.step_id)
        task = store.load_task(task_id)
        ttl = task.cache_ttl_seconds if task.cache_ttl_seconds is not None else DEFAULT_CACHE_TTL_SECONDS

        try:
            if step.description.lower().startswith("compile"):
                # Pure text synthesis over what's already in the scratchpad —
                # no browser needed, so this doesn't spin one up.
                table = llm.compile_comparison_table(task.scratchpad)
                result = {"summary": table, "final_url": None}
            else:
                target = _extract_target_url(step.description)
                fact_key = _normalize_url(target) if target.startswith("http") else None
                cached = store.get_fact(fact_key) if fact_key else None
                age = store.fact_age_seconds(fact_key) if fact_key else None

                if cached and age is not None and age < ttl:
                    result = dict(cached["value"])
                    result["cached"] = True
                    result["cache_age_seconds"] = round(age)
                    print(
                        f"[task {task_id}] step {step.step_index}: reusing cached "
                        f"result for {target} (learned {round(age)}s ago, from task "
                        f"{cached['source_task_id']}) — skipping browser"
                    )
                else:
                    # error from a previous failed attempt at THIS step, if
                    # any, so a retry/resume doesn't repeat the same dead end
                    result = run_step(
                        target, step.description, task.scratchpad, prior_error=step.error
                    )
                    if fact_key:
                        store.set_fact(fact_key, result, source_task_id=task_id)

            store.mark_step_done(step.step_id, result)
            store.append_scratchpad(
                task_id, f"[step {step.step_index} done] {step.description} -> {result}"
            )
            print(f"[task {task_id}] step {step.step_index} done")
        except KeyboardInterrupt:
            # Checkpoint immediately so this step is unambiguously "failed"
            # rather than stuck "in_progress" — belt-and-suspenders on top
            # of next_pending_step() also treating in_progress as resumable.
            store.mark_step_failed(step.step_id, "interrupted by user (Ctrl+C)")
            store.append_scratchpad(
                task_id, f"[step {step.step_index} INTERRUPTED] {step.description}"
            )
            store.set_task_status(task_id, "failed")
            print(f"\n[task {task_id}] interrupted during step {step.step_index} — "
                  f"checkpointed. Resume with: python cli.py resume {task_id}")
            raise
        except Exception as e:  # noqa: BLE001 - want to checkpoint on any failure
            store.mark_step_failed(step.step_id, str(e))
            store.append_scratchpad(
                task_id, f"[step {step.step_index} FAILED] {step.description} -> {e}"
            )
            print(f"[task {task_id}] step {step.step_index} FAILED: {e}", file=sys.stderr)
            print(
                f"[task {task_id}] stopping here — fix the issue and run "
                f"`python cli.py resume {task_id}` to retry from this step "
                f"(it will see the error above and try a different approach)"
            )
            store.set_task_status(task_id, "failed")
            return

        completed_since_compress += 1
        if completed_since_compress >= SCRATCHPAD_COMPRESS_EVERY_N_STEPS:
            task = store.load_task(task_id)
            compressed = llm.summarize_scratchpad(task.scratchpad)
            store.replace_scratchpad(task_id, compressed)
            completed_since_compress = 0
            print(f"[task {task_id}] scratchpad compressed")

    store.set_task_status(task_id, "done")
    print(f"[task {task_id}] all steps done")
    _write_output(task_id, store)


def _write_output(task_id: str, store: MemoryStore):
    task = store.load_task(task_id)
    out_path = Path("logs") / f"{task_id}_output.json"
    out_path.parent.mkdir(exist_ok=True)
    payload = {
        "task_id": task.task_id,
        "goal": task.goal,
        "steps": [
            {
                "description": s.description,
                "status": s.status,
                "result": s.result,
            }
            for s in task.steps
        ],
        "final_scratchpad": task.scratchpad,
    }
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"[task {task_id}] output written to {out_path}")

    # If the last step was a compile step, its result is already a Markdown
    # table — pull it out into its own .md file, since that's the artifact
    # worth showing on camera at the end of the demo.
    last_step = task.steps[-1] if task.steps else None
    if last_step and last_step.description.lower().startswith("compile") and last_step.result:
        table_text = last_step.result.get("summary", "")
        md_path = Path("logs") / f"{task_id}_comparison.md"
        md_path.write_text(f"# Report\n\n{table_text}\n")
        print(f"[task {task_id}] report written to {md_path}")