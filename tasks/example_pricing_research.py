"""
Sample task for the demo video: research pricing across a handful of SaaS
sites and end up with a comparison. Edit TARGET_URLS to whatever's relevant
for your demo — pick sites with a simple, stable /pricing page so the demo
is reliable on camera.

Run with:
    python cli.py start tasks/example_pricing_research.py
"""
from agent.planner import plan_pricing_research

GOAL = "Research pricing across competitor sites and build a comparison"

TARGET_URLS = [
    "https://www.notion.so/pricing",
    "https://linear.app/pricing",
    "https://www.figma.com/pricing/",
    # add more — more steps = more convincing "kill it mid-task" moment
]

# Optional: how long a cached result for a URL stays valid before this task
# re-scrapes it instead of reusing the cached fact. Omit to use the
# runner's default (see agent/runner.py DEFAULT_CACHE_TTL_SECONDS).
CACHE_TTL_SECONDS = 3600  # 1 hour — pricing pages don't change that often

STEPS = plan_pricing_research(TARGET_URLS)