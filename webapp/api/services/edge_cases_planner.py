"""Edge cases planner — empty states, errors, slow network, missing data, long content.

Sends ALL screens to the LLM in ONE batched call asking for edge case test cases per
screen. Quota-friendly: one call per plan generation regardless of project size.
"""
from __future__ import annotations

import json
import logging
import re

from utils.claude_client import DEFAULT_MODEL, ask

logger = logging.getLogger(__name__)


_PROMPT = """\
You are a senior product QA engineer. Generate edge-case test cases for a mobile app.
Focus on what happens when things GO WRONG, not the happy path.

FEATURE BEING TESTED:
{feature_description}

SCREENS IN THIS APP (with element counts):
{screens_json}

For each screen, generate 2-4 test cases covering these categories where applicable:
1. EMPTY STATE — what should happen if the data backing this screen is empty (no hotels, no results, no items)
2. ERROR STATE — server returns 5xx, network failure, timeout, partial data
3. SLOW NETWORK — loading indicators appear, no UI freezes, no duplicate taps register, retry possible
4. LONG CONTENT — extremely long names/descriptions don't break layout (truncation, ellipsis, wrapping)
5. MISSING FIELDS — optional fields not provided by backend (no photos, no rating, no price)

Output ONLY a JSON object — no markdown, no prose:
{{
  "cases": [
    {{
      "title": "<concise title>",
      "target_screen_name": "<screen name from input>",
      "category": "empty|error|slow_network|long_content|missing_fields",
      "scenario": "<what specifically goes wrong>",
      "expected_behavior": "<what the app SHOULD do>",
      "fail_criteria": "<what would make this fail>"
    }}
  ]
}}

RULES:
- target_screen_name MUST exactly match one of the screen names in the input. Do not invent screens.
- Generate 2-4 cases per screen — total around 20-50 for a typical project.
- Focus on screens that have meaningful state (hotel listings, hotel details, search results, booking screens).
- Skip cases that don't apply to a given screen (e.g. "empty state" doesn't apply to a static help page).
- Be SPECIFIC. "Verify error handling" is bad. "Verify a 'No hotels found in this city' empty state with a 'Try another city' button" is good.
"""


def generate_edge_cases_plan(
    feature_description: str,
    screens: list[dict],
) -> list[dict]:
    """Generate edge case test cases for all screens in one batched LLM call.

    Args:
        feature_description: feature being UAT'd (used as context for relevance)
        screens: list of dicts with {id, name, display_name, purpose, elements}

    Returns:
        list of canonical case dicts
    """
    if not screens:
        return []

    compact = [
        {
            "name": s["name"],
            "display_name": s.get("display_name") or s["name"],
            "purpose": s.get("purpose") or "",
            "element_count": len(s.get("elements") or []),
        }
        for s in screens
    ]

    prompt = _PROMPT.format(
        feature_description=feature_description,
        screens_json=json.dumps(compact, indent=2),
    )

    try:
        raw = ask(prompt=prompt, model=DEFAULT_MODEL, max_tokens=6000)
        parsed = _parse_json(raw)
    except Exception as exc:
        logger.warning(f"[EdgeCasesPlanner] Failed: {exc}")
        return []

    name_set = {s["name"] for s in screens}
    cases: list[dict] = []
    for c in parsed.get("cases", []):
        target = c.get("target_screen_name")
        if target not in name_set:
            continue  # Don't include hallucinated screens
        cases.append({
            "title": c.get("title", "Untitled edge case"),
            "target_screen_name": target,
            "navigation_path": [],
            "acceptance_criteria": _format_criteria(c),
            "branch_label": f"Edge case — {c.get('category', 'general')}",
        })

    logger.info(f"[EdgeCasesPlanner] Generated {len(cases)} edge case test cases")
    return cases


def _format_criteria(case: dict) -> str:
    parts = []
    if case.get("scenario"):
        parts.append(f"Scenario: {case['scenario']}")
    if case.get("expected_behavior"):
        parts.append(f"Expected: {case['expected_behavior']}")
    if case.get("fail_criteria"):
        parts.append(f"FAIL if: {case['fail_criteria']}")
    return " | ".join(parts) or "Verify the screen handles this edge case gracefully."


def _parse_json(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise
