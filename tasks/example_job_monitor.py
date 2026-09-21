"""
Second sample task, deliberately structured differently from the pricing
research one: each step has to search within the page (type + click) before
extracting anything, rather than just reading a static page. This is here
to prove the planner/runner/memory layer generalizes past one task shape.

Edit TARGET_URLS and KEYWORDS for your own demo. Pick boards with a simple
search box so it's reliable on camera.

Run with:
    python cli.py start tasks/example_job_monitor.py
"""
from agent.planner import plan_job_monitor

GOAL = "Monitor job boards for postings matching a search term"

TARGET_URLS = [
    "https://weworkremotely.com/",
    "https://remoteok.com/",
    # add more boards as needed
]

KEYWORDS = "python developer"

# Job listings change often — cache for a much shorter window than the
# pricing task's (see agent/runner.py DEFAULT_CACHE_TTL_SECONDS).
CACHE_TTL_SECONDS = 600  # 10 minutes

STEPS = plan_job_monitor(TARGET_URLS, KEYWORDS)