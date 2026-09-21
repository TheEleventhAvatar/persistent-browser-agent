-- One row per top-level task (e.g. "research competitor pricing")
CREATE TABLE IF NOT EXISTS tasks (
    task_id           TEXT PRIMARY KEY,
    goal              TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'pending',   -- pending | in_progress | done | failed
    cache_ttl_seconds INTEGER,                            -- NULL = use runner default
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

-- One row per sub-goal in a task's plan (the task graph, flattened to an
-- ordered list — swap for an edge table if you need branching/parallel steps)
CREATE TABLE IF NOT EXISTS steps (
    step_id     TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL,
    step_index  INTEGER NOT NULL,
    description TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',   -- pending | in_progress | done | failed
    result      TEXT,                              -- JSON string of whatever the step produced
    error       TEXT,                               -- last error message, if failed
    attempts    INTEGER NOT NULL DEFAULT 0,
    updated_at  TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);

-- Running working memory for a task. Kept short via periodic LLM
-- summarization so long tasks don't blow the context window on resume.
CREATE TABLE IF NOT EXISTS scratchpad (
    task_id     TEXT PRIMARY KEY,
    content     TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL,
    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
);

-- Cross-task memory: facts learned once (e.g. "what's on Notion's pricing
-- page") that later, separate task runs can reuse instead of re-scraping,
-- as long as the fact isn't stale. This is what makes memory persist
-- BETWEEN tasks, not just within one task's resume.
CREATE TABLE IF NOT EXISTS facts (
    fact_key        TEXT PRIMARY KEY,
    value           TEXT NOT NULL,   -- JSON string of whatever was learned
    source_task_id  TEXT,
    updated_at      TEXT NOT NULL
);