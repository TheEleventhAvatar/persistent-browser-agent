

https://github.com/user-attachments/assets/01c12db0-3628-4d5d-9c14-b570370ee5d2

# Persistent Browser Agent

A browser-automation agent that can be handed a multi-step task, execute it with
Playwright, survive being killed or closing the tab mid-task, and **resume exactly
where it left off** — remembering completed sub-goals, partial results, and its
working scratchpad, without re-doing work or blowing the LLM context window.

It also has real **cross-task memory** (a second run reuses data a prior run
already learned, instead of re-scraping), **structured browser actions**
(click/type/select/scroll, not just "click this text"), and
**error-adaptive retries** (a failed step's error is fed back to the model so
a retry doesn't repeat the same dead end). A live web dashboard shows all of
this happening in real time.

This is a starter scaffold, not a finished product, but it's no longer a toy —
every mechanism below is tested end to end.

## Why this exists

Most "browser agent" demos show a bot clicking around once, end to end, with no
interruption. The interesting (and hard) part is state: what happens when the
task is long, the process dies, the page breaks, or you already know part of
the answer from an hour ago. This scaffold focuses on that.

## Architecture

```
persistent-browser-agent/
├── agent/
│   ├── planner.py        # breaks a goal into a task graph (sub-goals)
│   ├── browser_agent.py  # Playwright executor: runs one structured action at a time
│   ├── runner.py         # orchestrates planner + executor + checkpointing + fact cache
│   ├── llm.py            # LLM calls: next action (structured JSON), summarize, compile
│   └── dashboard.py       # zero-dependency live web UI (stdlib http.server)
├── memory/
│   ├── store.py           # SQLite: tasks, steps, scratchpad, cross-task facts
│   └── schema.sql          # table definitions
├── tasks/
│   ├── example_pricing_research.py  # task 1: read static pricing pages
│   └── example_job_monitor.py        # task 2: search-then-extract (structurally different)
├── logs/                  # run logs + compiled reports land here
├── cli.py                 # start / resume / list / dashboard
├── requirements.txt
└── .env.example
```

### How resume works

1. Every task is broken into a **task graph**: an ordered list of sub-goals
   (e.g. "visit competitor A pricing page", "visit competitor B...", "compile
   comparison table").
2. Each sub-goal, when completed, is written to SQLite (`memory/store.py`) with
   its result and a status (`pending`, `in_progress`, `done`, `failed`).
3. A running **scratchpad** (free-text working memory) is updated after each
   step and periodically **summarized/compressed** by the LLM so long tasks
   don't overflow context — you keep the gist, not the full transcript.
4. On `python cli.py resume <task_id>`, the runner loads the task graph +
   scratchpad from SQLite, skips every sub-goal marked `done`, and continues
   from the first `pending`/`failed`/`in_progress` one (a step interrupted
   mid-flight — Ctrl+C, crash, closed terminal — is left `in_progress` with
   nothing actually running, so it's treated as resumable too, not silently
   dropped). The agent says out loud how much was already done
   ("resuming: 6 of 15 already done") so it's obvious on camera.

### How error-adaptive retry works

If a step fails (selector broke, page didn't load, element not found), it's
checkpointed as `failed` with the error message. On the next attempt — whether
that's an automatic retry within the same run or a manual `resume` later —
that exact error is passed back into the model's next decision as
`prior_error`, so it's explicitly told "this didn't work, try something else"
instead of blindly repeating the same click. There are two layers of this:
within a single step (a failed click informs the very next action in that same
browser session) and across whole runs (a step that failed last time informs
the retry attempt).

### How cross-task memory works

Every extracted result is also written to a `facts` table keyed by a
normalized URL, independent of any one task. Before a step re-browses a URL,
the runner checks whether a fresh-enough fact already exists for it — if so,
it reuses that result and skips the browser entirely, logging
`reusing cached result... skipping browser` with how old the data is and
which task originally learned it. "Fresh enough" is a per-task TTL (pricing
data is cached for an hour by default; job listings, which go stale faster,
for 10 minutes) — set via `CACHE_TTL_SECONDS` in a task file. This is what
makes memory persist **between** separate task runs, not just within one
task's resume.

### How the browser actions work

The model doesn't get to say "click that" in plain English — it returns one
structured JSON action per turn:
```json
{"action": "click", "target": "Pricing"}
{"action": "type", "target": "Search jobs", "value": "python developer"}
{"action": "select", "target": "Country", "value": "United States"}
{"action": "scroll", "direction": "down"}
{"action": "done", "result": "<final extracted answer>"}
```
`browser_agent.py` executes each action type with a few DOM-lookup fallback
strategies (role → label → placeholder → visible text), so it's not relying
on one exact selector matching.

## Setup

```bash
cd persistent-browser-agent
python -m venv venv && source venv/Scripts/activate   # Mac/Linux: venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env   # add your OPENAI_API_KEY
```

Uses `gpt-4o-mini` by default (see `agent/llm.py`) — cheap enough to run many
full demo iterations on a $1–2 credit balance. A single pricing-research run
across 3 sites is a few cents at most. Bump `MODEL` to `gpt-4o` in
`agent/llm.py` if you want stronger decisions and don't mind the extra cost.

## Running it

**Start the dashboard first** and leave it open in a browser tab — this is
what you actually watch:
```bash
python cli.py dashboard
```

**In a second terminal**, start a task:
```bash
python cli.py start tasks/example_pricing_research.py
```
or the structurally different second task:
```bash
python cli.py start tasks/example_job_monitor.py
```

Kill it mid-run (Ctrl+C, or close the terminal — that's the point). Then:
```bash
python cli.py resume <task_id>
```
(the task_id is printed when you start, and `python cli.py list` shows all runs)

Run the same task file again later and watch it skip straight past any URL it
already has fresh data for.

## Extending this

- Swap `agent/browser_agent.py`'s Playwright calls for a computer-use API if
  you want screenshot+click grounding instead of DOM selectors.
- Replace SQLite with Postgres/Redis if you want multi-agent or multi-machine
  runs, or to share the fact cache across machines.
- Add a bounded automatic-retry loop in `runner.py` (currently a failed step
  stops the task and waits for a manual `resume`) so it self-heals without
  a human in the loop, up to N attempts.
- Add a third, even more different task shape (e.g. one requiring login) to
  further stress-test the planner/action schema.

## Notes

- This scaffold is intentionally minimal — no framework (no LangChain/AutoGen)
  so it's easy to explain in an interview: "here's exactly how the state
  machine works" beats "here's a framework I configured."
- `agent/llm.py` calls the OpenAI API directly. Set `OPENAI_API_KEY` in
  `.env`. Swapping to Anthropic or another provider later only means editing
  this one file — nothing else in the project depends on which LLM you use.
- The dashboard (`agent/dashboard.py`) has zero extra dependencies — it's
  Python's built-in `http.server`, polling the same SQLite file the CLI
  writes to. No Flask, no build step.
