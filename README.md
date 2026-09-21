# Persistent Browser Agent

A browser-automation agent that can be handed a multi-step task, execute it with
Playwright, survive being killed or closing the tab mid-task, and **resume exactly
where it left off** — remembering completed sub-goals, partial results, and its
working scratchpad, without re-doing work or blowing the LLM context window.

This is a starter scaffold, not a finished product. It's built to be extended
and to produce a good demo video quickly. See "Demo script" below.

## Why this exists

Most "browser agent" demos show a bot clicking around once, end to end, with no
interruption. The interesting (and hard) part is state: what happens when the
task is long, the process dies, the page breaks, or you just want to check on
it tomorrow. This scaffold focuses on that.

## Architecture

```
persistent-browser-agent/
├── agent/
│   ├── planner.py       # breaks a goal into a task graph (sub-goals)
│   ├── browser_agent.py # Playwright-driven executor for one sub-goal
│   ├── runner.py        # orchestrates planner + executor + checkpointing
│   └── llm.py           # thin wrapper around the LLM calls (plan, act, summarize)
├── memory/
│   ├── store.py         # SQLite-backed state store (tasks, steps, scratchpad)
│   └── schema.sql       # table definitions
├── tasks/
│   └── example_pricing_research.py  # sample multi-step task definition
├── logs/                # run logs land here
├── cli.py               # `python cli.py start ...` / `python cli.py resume ...`
├── requirements.txt
└── .env.example
```

### How resume works

1. Every task is broken into a **task graph**: an ordered list of sub-goals
   (e.g. "visit competitor A pricing page", "extract plan tiers + prices",
   "visit competitor B...", "compile comparison table").
2. Each sub-goal, when completed, is written to SQLite (`memory/store.py`) with
   its result and a status (`pending`, `in_progress`, `done`, `failed`).
3. A running **scratchpad** (free-text working memory) is updated after each
   step and periodically **summarized/compressed** by the LLM so long tasks
   don't overflow context — you keep the gist, not the full transcript.
4. On `python cli.py resume <task_id>`, the runner loads the task graph +
   scratchpad from SQLite, skips every sub-goal marked `done`, and continues
   from the first `pending`/`failed` one. The agent literally says out loud
   ("resuming: 6 of 15 already done") so it's obvious on camera.
5. If a step fails (selector broke, page didn't load, CAPTCHA, etc.) it's
   marked `failed` with an error note in the scratchpad, and the runner can
   retry it with that context instead of starting blind.

## Setup

```bash
cd persistent-browser-agent
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env   # add your OPENAI_API_KEY
```

## Running it

Start a new task:
```bash
python cli.py start tasks/example_pricing_research.py
```

Kill it mid-run (Ctrl+C, or close the terminal — that's the point). Then:
```bash
python cli.py resume <task_id>
```
(the task_id is printed when you start, and `python cli.py list` shows all runs)

## Demo script (for the video)

This is the sequence that makes the "not just a scripted bot" point land in
under 90 seconds:

1. **0:00–0:15** — One line, on camera: "I'm giving it a real multi-step task:
   research pricing across N sites and build a comparison doc." Run `start`.
2. **0:15–0:45** — Show the browser window actually navigating, clicking,
   extracting data on 2–3 sites. Overlay or terminal shows sub-goals ticking
   off (`[done] extract pricing: competitor A`).
3. **0:45–0:55** — Mid-task, kill the process (Ctrl+C) or close the browser
   tab. Make this visible and deliberate.
4. **0:55–1:10** — Run `resume`. The agent prints something like
   `resuming task 4f2a — 6/15 sub-goals already done, continuing from
   "competitor G"`. This is the moment the video is built around.
5. **1:10–1:30** — Let it finish, then show the compiled output file
   (comparison table / doc) that was assembled from memory across the whole
   run, including the part done before the kill.

Optional bonus line for the README/description, not the video: mention any
inference-cost savings from caching repeated page-analysis calls or trimming
scratchpad context before each LLM call.

## Extending this

- Swap `agent/browser_agent.py`'s Playwright calls for a computer-use API if
  you want screenshot+click grounding instead of DOM selectors.
- Replace SQLite with Postgres/Redis if you want multi-agent or multi-machine
  runs.
- Add a self-correction loop in `runner.py`: on `failed` status, feed the
  error + page screenshot back to the planner to re-plan just that sub-goal.
- Add a second task type (e.g. "daily job-board monitor") to show the memory
  layer generalizes beyond one demo.

## Notes

- This scaffold is intentionally minimal — no framework (no LangChain/AutoGen)
  so it's easy to explain in an interview: "here's exactly how the state
  machine works" beats "here's a framework I configured."
- `agent/llm.py` calls the OPEN API directly. Set `OPEN_API_KEY` in
  `.env`.
