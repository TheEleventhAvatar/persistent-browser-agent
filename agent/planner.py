"""
Turns a high-level goal + a list of target URLs into an ordered list of
sub-goal descriptions (the flattened task graph stored in `steps`).

Kept deliberately simple: one sub-goal per URL/action. Real version could
call the LLM to generate the plan dynamically, or support branching
(if/else sub-goals) instead of a flat list — that's the natural next
extension once this works end to end.
"""
from __future__ import annotations


def plan_pricing_research(urls: list[str]) -> list[str]:
    steps = []
    for url in urls:
        steps.append(f"Visit {url} and extract pricing tiers + prices")
    steps.append("Compile all extracted pricing data into a comparison table")
    return steps


def plan_job_monitor(urls: list[str], keywords: str) -> list[str]:
    """
    Structurally different from plan_pricing_research: each step involves
    an in-page search action (type + click) before extraction, not just a
    read of a static page. Demonstrates the planner/memory layer isn't
    hardcoded to one task shape.
    """
    steps = []
    for url in urls:
        steps.append(
            f"Visit {url}, search for '{keywords}' if there's a search field, "
            f"and list any matching job postings with their title and a short detail"
        )
    steps.append(
        "Compile all matching job postings found across every board into one report, "
        "grouped by source site"
    )
    return steps