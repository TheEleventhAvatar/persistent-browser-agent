"""
A local, zero-dependency web dashboard for watching a task run live and
seeing exactly what "resume" does.

No Flask, no extra pip installs — just Python's built-in http.server. It
reads directly from the same SQLite file the CLI/runner writes to, so start
a task in one terminal, open the dashboard in a browser, and watch the step
timeline light up in real time. This is what you want on screen for the
demo video instead of a scrolling terminal.

Run with:
    python cli.py dashboard
then leave it open and run tasks/kill/resume in a second terminal.
"""
from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from memory.store import MemoryStore

DEFAULT_PORT = 8420

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Persistent Browser Agent — Live</title>
<style>
  :root {
    --bg: #0b0d12;
    --panel: #12151c;
    --panel-border: #23283350;
    --text: #e7e9ee;
    --muted: #8b93a7;
    --accent: #5b8cff;
    --done: #34d399;
    --in-progress: #f5c451;
    --failed: #f2555a;
    --pending: #3a4152;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font-family: -apple-system, "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    display: flex;
    height: 100vh;
    overflow: hidden;
  }
  aside {
    width: 300px;
    border-right: 1px solid var(--panel-border);
    background: var(--panel);
    display: flex;
    flex-direction: column;
    overflow-y: auto;
    flex-shrink: 0;
  }
  aside h1 {
    font-size: 14px;
    font-weight: 600;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.06em;
    padding: 18px 16px 8px;
    margin: 0;
  }
  .task-item {
    padding: 12px 16px;
    cursor: pointer;
    border-left: 3px solid transparent;
    border-bottom: 1px solid var(--panel-border);
  }
  .task-item:hover { background: #1a1f2b; }
  .task-item.selected { border-left-color: var(--accent); background: #171c28; }
  .task-item .goal { font-size: 13px; line-height: 1.4; margin-bottom: 6px; }
  .task-item .meta { font-size: 11px; color: var(--muted); display: flex; justify-content: space-between; }
  main {
    flex: 1;
    overflow-y: auto;
    padding: 28px 36px;
  }
  .empty { color: var(--muted); font-size: 14px; margin-top: 40px; }
  .task-header { margin-bottom: 22px; }
  .task-header .goal { font-size: 20px; font-weight: 600; margin-bottom: 6px; }
  .task-header .id { font-size: 12px; color: var(--muted); font-family: ui-monospace, monospace; }
  .badge {
    display: inline-block;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    padding: 3px 9px;
    border-radius: 20px;
    margin-left: 8px;
  }
  .badge.done { background: #12331f; color: var(--done); }
  .badge.in_progress { background: #3a2d0b; color: var(--in-progress); }
  .badge.failed { background: #3a1213; color: var(--failed); }
  .badge.pending { background: #1c2130; color: var(--muted); }

  .progress-bar {
    height: 6px;
    background: var(--pending);
    border-radius: 4px;
    overflow: hidden;
    margin: 14px 0 26px;
  }
  .progress-fill {
    height: 100%;
    background: linear-gradient(90deg, var(--accent), var(--done));
    transition: width 0.4s ease;
  }

  .steps { display: flex; flex-direction: column; gap: 10px; margin-bottom: 30px; }
  .step {
    display: flex;
    align-items: flex-start;
    gap: 14px;
    padding: 14px 16px;
    background: var(--panel);
    border: 1px solid var(--panel-border);
    border-radius: 10px;
  }
  .step.in_progress { border-color: var(--in-progress); animation: pulse 1.4s ease-in-out infinite; }
  .step.failed { border-color: var(--failed); }
  .step.done { border-color: #1f3b2c; }
  @keyframes pulse {
    0%, 100% { box-shadow: 0 0 0 0 rgba(245, 196, 81, 0.0); }
    50% { box-shadow: 0 0 0 4px rgba(245, 196, 81, 0.12); }
  }
  .dot {
    width: 22px; height: 22px; border-radius: 50%;
    flex-shrink: 0; margin-top: 2px;
    display: flex; align-items: center; justify-content: center;
    font-size: 12px; font-weight: 700;
  }
  .dot.done { background: var(--done); color: #08150e; }
  .dot.in_progress { background: var(--in-progress); color: #241c04; }
  .dot.failed { background: var(--failed); color: #240707; }
  .dot.pending { background: var(--pending); color: var(--muted); }
  .step-body { flex: 1; min-width: 0; }
  .step-desc { font-size: 14px; margin-bottom: 4px; }
  .step-result {
    font-size: 12px; color: var(--muted);
    background: #0d1017; border-radius: 6px; padding: 8px 10px;
    margin-top: 8px; white-space: pre-wrap; word-break: break-word;
    font-family: ui-monospace, monospace; max-height: 160px; overflow-y: auto;
  }
  .step-error { font-size: 12px; color: var(--failed); margin-top: 6px; }

  h2.section-title {
    font-size: 12px; text-transform: uppercase; letter-spacing: 0.06em;
    color: var(--muted); margin: 0 0 10px;
  }
  .scratchpad {
    background: var(--panel); border: 1px solid var(--panel-border);
    border-radius: 10px; padding: 16px;
    font-family: ui-monospace, monospace; font-size: 12px;
    white-space: pre-wrap; color: #c7cbd6; max-height: 320px; overflow-y: auto;
  }
  .live-dot {
    display: inline-block; width: 8px; height: 8px; border-radius: 50%;
    background: var(--done); margin-right: 6px;
    animation: blink 1.6s ease-in-out infinite;
  }
  @keyframes blink { 0%, 100% { opacity: 1; } 50% { opacity: 0.25; } }
  footer { color: var(--muted); font-size: 11px; padding: 10px 16px; }
</style>
</head>
<body>
  <aside>
    <h1>Tasks</h1>
    <div id="task-list"></div>
    <footer><span class="live-dot"></span>auto-refreshing</footer>
  </aside>
  <main id="main">
    <div class="empty">Select a task, or start one with:
      <br /><code>python cli.py start tasks/example_pricing_research.py</code></div>
  </main>

<script>
let selectedId = null;

function badge(status) {
  return `<span class="badge ${status}">${status.replace('_',' ')}</span>`;
}

function renderTaskList(tasks) {
  const el = document.getElementById('task-list');
  if (!tasks.length) {
    el.innerHTML = '<div style="padding:16px;color:var(--muted);font-size:13px;">no tasks yet</div>';
    return;
  }
  if (!selectedId) selectedId = tasks[0].task_id;
  el.innerHTML = tasks.map(t => `
    <div class="task-item ${t.task_id === selectedId ? 'selected' : ''}" onclick="selectTask('${t.task_id}')">
      <div class="goal">${t.goal}</div>
      <div class="meta"><span>${t.task_id}</span>${badge(t.status)}</div>
    </div>
  `).join('');
}

function renderMain(task) {
  const main = document.getElementById('main');
  if (!task) {
    main.innerHTML = '<div class="empty">Task not found.</div>';
    return;
  }
  const done = task.steps.filter(s => s.status === 'done').length;
  const pct = task.steps.length ? Math.round(100 * done / task.steps.length) : 0;

  const stepsHtml = task.steps.map((s, i) => {
    const dotContent = s.status === 'done' ? '✓' : s.status === 'failed' ? '!' : (i + 1);
    let resultHtml = '';
    if (s.result && s.result.summary) {
      const cachedNote = s.result.cached
        ? `<span style="color:#5b8cff;font-weight:600;"> ⚡ cached, ${s.result.cache_age_seconds}s old</span>`
        : '';
      resultHtml = `<div class="step-result">${escapeHtml(s.result.summary)}</div><div style="font-size:11px;margin-top:4px;">${cachedNote}</div>`;
    }
    let errorHtml = s.error ? `<div class="step-error">⚠ ${escapeHtml(s.error)}</div>` : '';
    return `
      <div class="step ${s.status}">
        <div class="dot ${s.status}">${dotContent}</div>
        <div class="step-body">
          <div class="step-desc">${escapeHtml(s.description)}</div>
          ${errorHtml}
          ${resultHtml}
        </div>
      </div>
    `;
  }).join('');

  main.innerHTML = `
    <div class="task-header">
      <div class="goal">${escapeHtml(task.goal)} ${badge(task.status)}</div>
      <div class="id">${task.task_id}</div>
    </div>
    <div class="progress-bar"><div class="progress-fill" style="width:${pct}%"></div></div>
    <h2 class="section-title">${done} / ${task.steps.length} sub-goals done</h2>
    <div class="steps">${stepsHtml}</div>
    <h2 class="section-title">Working memory (scratchpad)</h2>
    <div class="scratchpad">${escapeHtml(task.scratchpad || '(empty)')}</div>
  `;
}

function escapeHtml(str) {
  const d = document.createElement('div');
  d.innerText = String(str);
  return d.innerHTML;
}

function selectTask(id) {
  selectedId = id;
  refresh();
}

async function refresh() {
  const tasks = await (await fetch('/api/tasks')).json();
  renderTaskList(tasks);
  if (selectedId) {
    const task = await (await fetch('/api/task?id=' + encodeURIComponent(selectedId))).json();
    renderMain(task);
  }
}

refresh();
setInterval(refresh, 1000);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    store: MemoryStore  # set on the class before serving

    def log_message(self, fmt, *args):
        pass  # keep the terminal quiet; the dashboard is the visual

    def _send_json(self, payload, status=200):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/":
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path == "/api/tasks":
            tasks = self.store.list_tasks()
            self._send_json(tasks)
            return

        if parsed.path == "/api/task":
            qs = parse_qs(parsed.query)
            task_id = (qs.get("id") or [""])[0]
            try:
                task = self.store.load_task(task_id)
            except KeyError:
                self._send_json({"error": "not found"}, status=404)
                return
            self._send_json(
                {
                    "task_id": task.task_id,
                    "goal": task.goal,
                    "status": task.status,
                    "scratchpad": task.scratchpad,
                    "steps": [
                        {
                            "description": s.description,
                            "status": s.status,
                            "result": s.result,
                            "error": s.error,
                        }
                        for s in task.steps
                    ],
                }
            )
            return

        self.send_response(404)
        self.end_headers()


def run_dashboard(port: int = DEFAULT_PORT, open_browser: bool = True):
    Handler.store = MemoryStore()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"dashboard running at {url}  (Ctrl+C to stop)")

    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\ndashboard stopped")