"""
Thin wrapper around the OpenAI API. Kept in one place so it's easy to swap
models, add caching, or add the "inference optimization" bonus later (e.g.
trimming scratchpad context before each call, caching repeated
page-analysis prompts).

Using gpt-4o-mini by default since it's cheap enough to run many demo
iterations on a small credit balance. Swap MODEL to gpt-4o if you want
stronger decisions and don't mind the extra cost.
"""
from __future__ import annotations

import json
import os

from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

MODEL = "gpt-4o-mini"

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY not set. Copy .env.example to .env and fill it in."
            )
        _client = OpenAI(api_key=api_key)
    return _client


def complete(system: str, user: str, max_tokens: int = 1000) -> str:
    """Single-turn completion. Returns plain text."""
    client = _get_client()
    resp = client.chat.completions.create(
        model=MODEL,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return resp.choices[0].message.content or ""


def summarize_scratchpad(scratchpad: str, max_tokens: int = 400) -> str:
    """
    Compress the running working memory. Called periodically by the runner
    so a long task doesn't blow the context window on resume — you keep the
    gist (key findings, decisions, blockers) rather than the full transcript.
    """
    if not scratchpad.strip():
        return scratchpad
    system = (
        "You compress an AI agent's working memory. Keep concrete facts, "
        "findings, and decisions. Drop narration and filler. Output plain "
        "text notes, not prose paragraphs."
    )
    user = f"Compress this working memory, keeping anything a resumed task would need:\n\n{scratchpad}"
    return complete(system, user, max_tokens=max_tokens)


def decide_next_action(
    step_description: str,
    page_summary: str,
    scratchpad: str,
    prior_error: str | None = None,
) -> dict:
    """
    Ask the model what to do next for a given sub-goal, given a text summary
    of the current page state and the task's working memory. Returns a
    structured action dict the browser_agent executor can act on directly:

        {"action": "click", "target": "Pricing"}
        {"action": "type", "target": "Search jobs", "value": "python developer"}
        {"action": "select", "target": "Country", "value": "United States"}
        {"action": "scroll", "direction": "down"}
        {"action": "wait", "seconds": 2}
        {"action": "done", "result": "<final extracted answer>"}

    `target` should be the element's visible text, label, or placeholder —
    whatever a human would read to find it. If `prior_error` is given (e.g.
    a failed click from a previous attempt, or the error a previous run of
    this step ended on), the model is told what went wrong so it can try a
    different approach instead of repeating the same mistake.
    """
    system = (
        "You are the decision step of a browser automation agent. Given a "
        "sub-goal, the current page state, and working memory, respond with "
        "ONE next action as STRICT JSON matching one of these shapes, and "
        "nothing else — no prose, no code fences:\n"
        '{"action": "click", "target": "<visible text/label of element>"}\n'
        '{"action": "type", "target": "<field label/placeholder>", "value": "<text to enter>"}\n'
        '{"action": "select", "target": "<field label>", "value": "<option to choose>"}\n'
        '{"action": "scroll", "direction": "down"}\n'
        '{"action": "wait", "seconds": 2}\n'
        '{"action": "done", "result": "<final extracted answer for the sub-goal>"}\n'
        "Use 'done' as soon as the sub-goal is satisfied by what's already "
        "visible on the page — don't take extra actions. If a prior attempt "
        "failed, do not repeat the same target/approach; try something "
        "different (a different link, scrolling first, a synonym for the "
        "label, etc)."
    )
    user_parts = [
        f"Sub-goal: {step_description}",
        f"\nCurrent page state:\n{page_summary}",
        f"\nWorking memory so far:\n{scratchpad or '(empty)'}",
    ]
    if prior_error:
        user_parts.append(
            f"\nA previous attempt at this sub-goal failed with: {prior_error}\n"
            f"Adapt your approach instead of repeating it."
        )
    user = "\n".join(user_parts)

    raw = complete(system, user, max_tokens=200)
    return _parse_action(raw)


def _parse_action(raw: str) -> dict:
    """
    Best-effort JSON parse of the model's action response. Falls back to a
    'click' on the raw text if the model didn't return valid JSON, and to a
    safe 'wait' if even that looks wrong — the executor should never crash
    just because the model's output was slightly malformed.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and "action" in parsed:
            return parsed
    except json.JSONDecodeError:
        pass
    # Fallback: treat the raw text as a click target, same as the old
    # freeform behavior, so a slightly-off model response degrades
    # gracefully instead of crashing the step.
    cleaned = text.strip().strip('"')
    if cleaned.upper().startswith("DONE:"):
        return {"action": "done", "result": cleaned.split(":", 1)[1].strip()}
    return {"action": "click", "target": cleaned}


def compile_comparison_table(scratchpad: str, max_tokens: int = 600) -> str:
    """
    Pure text synthesis — no browser needed. Takes the scratchpad (which
    holds every prior step's extracted result) and turns it into a clean
    Markdown table. Called directly by the runner for "compile" steps
    instead of routing through browser_agent.run_step.
    """
    system = (
        "You turn extracted pricing data into a clean Markdown comparison "
        "table. Output ONLY the Markdown table (with a header row and one "
        "row per plan/tier per company), nothing else — no preamble, no "
        "explanation, no code fences."
    )
    user = f"Extracted data:\n\n{scratchpad}"
    return complete(system, user, max_tokens=max_tokens)