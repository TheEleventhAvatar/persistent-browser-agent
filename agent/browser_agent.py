"""
Executes ONE sub-goal in a real browser via Playwright.

The LLM (agent/llm.py) decides one structured action at a time — click,
type, select, scroll, wait, or done — based on a text summary of the
current page. This file is the "hands": it takes that structured action and
actually performs it, with a few DOM-lookup fallback strategies per action
type so it's not relying on exact text matches alone.

Two layers of error-adaptive retry:
  - WITHIN a step: if an action fails (element not found, timeout), the
    failure is fed back into the *next* decision call in the same run, so
    the model tries something different instead of repeating the same
    action blindly.
  - ACROSS runs: if the whole step failed last time (see runner.py), the
    error from that attempt is passed in as `prior_error` and included in
    the very first decision call, so a resumed/retried step doesn't repeat
    a dead end.
"""
from __future__ import annotations

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from . import llm

MAX_ACTIONS_PER_STEP = 8


def _page_text_summary(page, max_chars: int = 3000) -> str:
    """Cheap, dependency-free page summary: title + visible text, truncated."""
    try:
        title = page.title()
    except Exception:
        title = "(unknown)"
    try:
        text = page.inner_text("body")
    except Exception:
        text = "(could not read page body)"
    text = " ".join(text.split())
    return f"Title: {title}\nURL: {page.url}\nVisible text (truncated): {text[:max_chars]}"


def _find_clickable(page, target: str):
    """Try a few strategies to find a clickable element by human-readable text."""
    for locator in (
        page.get_by_role("link", name=target, exact=False),
        page.get_by_role("button", name=target, exact=False),
        page.get_by_text(target, exact=False),
    ):
        try:
            if locator.count() > 0:
                return locator.first
        except Exception:
            continue
    return None


def _find_input(page, target: str):
    """Try a few strategies to find a text input/textarea by label/placeholder/name."""
    for locator in (
        page.get_by_label(target, exact=False),
        page.get_by_placeholder(target, exact=False),
        page.locator(f"input[name*='{target}' i], textarea[name*='{target}' i]"),
    ):
        try:
            if locator.count() > 0:
                return locator.first
        except Exception:
            continue
    return None


def _do_action(page, action: dict) -> str | None:
    """
    Executes one structured action against the page. Returns None on
    success, or a short error string on failure (caller decides whether to
    retry with updated context rather than crashing the whole step).
    """
    kind = action.get("action")

    if kind == "click":
        target = action.get("target", "")
        el = _find_clickable(page, target)
        if el is None:
            return f"could not find a clickable element matching '{target}'"
        try:
            el.click(timeout=5000)
            page.wait_for_load_state("domcontentloaded", timeout=10000)
            return None
        except PWTimeout:
            return f"click on '{target}' timed out"

    if kind == "type":
        target = action.get("target", "")
        value = action.get("value", "")
        el = _find_input(page, target)
        if el is None:
            return f"could not find an input matching '{target}'"
        try:
            el.fill(value, timeout=5000)
            return None
        except PWTimeout:
            return f"typing into '{target}' timed out"

    if kind == "select":
        target = action.get("target", "")
        value = action.get("value", "")
        try:
            el = page.get_by_label(target, exact=False)
            if el.count() == 0:
                return f"could not find a select matching '{target}'"
            el.first.select_option(label=value, timeout=5000)
            return None
        except Exception as e:  # noqa: BLE001
            return f"selecting '{value}' in '{target}' failed: {e}"

    if kind == "scroll":
        direction = action.get("direction", "down")
        try:
            delta = 800 if direction == "down" else -800
            page.mouse.wheel(0, delta)
            page.wait_for_timeout(300)
            return None
        except Exception as e:  # noqa: BLE001
            return f"scroll failed: {e}"

    if kind == "wait":
        seconds = action.get("seconds", 1)
        try:
            page.wait_for_timeout(min(seconds, 5) * 1000)
            return None
        except Exception as e:  # noqa: BLE001
            return f"wait failed: {e}"

    return f"unrecognized action type: {kind!r}"


def run_step(
    url_or_instruction: str,
    step_description: str,
    scratchpad: str,
    prior_error: str | None = None,
) -> dict:
    """
    Executes a single sub-goal starting from a URL. Returns a dict result,
    e.g. {"summary": "...", "final_url": "..."} — whatever the LLM decided
    the sub-goal produced.

    `prior_error` carries the failure reason from a previous attempt at this
    exact step (set by runner.py on retry/resume), so the model doesn't
    repeat a dead end. Raises on failure so the caller can mark the step
    `failed` with the error message and retry with that context next time.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            if url_or_instruction.startswith("http"):
                page.goto(url_or_instruction, timeout=20000, wait_until="domcontentloaded")

            last_error = prior_error
            for _ in range(MAX_ACTIONS_PER_STEP):
                page_summary = _page_text_summary(page)
                action = llm.decide_next_action(
                    step_description, page_summary, scratchpad, prior_error=last_error
                )

                if action.get("action") == "done":
                    return {
                        "summary": action.get("result", ""),
                        "final_url": page.url,
                    }

                error = _do_action(page, action)
                if error:
                    # Don't crash on one bad action — feed the failure back
                    # in as context for the *next* decision in this same
                    # run, so the model adapts instead of repeating it.
                    last_error = error
                    continue
                last_error = None

            raise RuntimeError(
                f"exceeded {MAX_ACTIONS_PER_STEP} actions without reaching 'done' "
                f"for step: {step_description}"
                + (f" (last issue: {last_error})" if last_error else "")
            )
        finally:
            browser.close()