"""Deeplink / utility integrity planner.

Walks the project graph and emits test cases for each structural issue:
- Orphan screens (no incoming edges) — likely only reachable via deeplink
- Dead-end screens (no outgoing edges) — verify intentional vs stuck
- Dangling leads_to_hints — element references a non-existent screen name
- Unreachable screens — no path from home

Pure deterministic. Zero LLM calls. Uses webapp.api.services.graph_analyzer.
"""
from __future__ import annotations

import logging

from webapp.api.services.graph_analyzer import (
    find_dangling_hints,
    find_dead_end_screens,
    find_orphan_screens,
    find_unreachable_screens,
)

logger = logging.getLogger(__name__)


def generate_deeplink_utility_plan(
    screens: list[dict],
    edges: list[dict],
    home_screen_id: int | None = None,
) -> list[dict]:
    """Build deeplink + navigation integrity test cases by analyzing the screen graph.

    Args:
        screens: list of dicts {id, name, display_name, elements}
        edges: list of dicts {from_screen_id, to_screen_id, trigger}
        home_screen_id: optional id of the project's home screen — used to detect
                        unreachable screens. If None, we just skip that check.

    Returns:
        list of test case dicts
    """
    if not screens:
        return []

    cases: list[dict] = []

    # 1. Orphan screens — no incoming edges
    orphans = find_orphan_screens(screens, edges, home_screen_id=home_screen_id)
    for s in orphans:
        cases.append({
            "title": f"Verify '{s.get('display_name') or s['name']}' is reachable",
            "target_screen_name": s["name"],
            "navigation_path": [],
            "acceptance_criteria": (
                f"This screen has NO incoming edges in the navigation graph — it should "
                f"either be reachable via a deeplink (e.g. mmt://...) or via a flow that "
                f"hasn't been mapped yet. Verify the entry point exists and works. "
                f"FAIL if: there is no way to reach this screen at all."
            ),
            "branch_label": "Deeplink — orphan screens",
        })

    # 2. Dead-end screens — no outgoing edges
    dead_ends = find_dead_end_screens(screens, edges)
    for s in dead_ends:
        cases.append({
            "title": f"Verify '{s.get('display_name') or s['name']}' is intentionally a terminal screen",
            "target_screen_name": s["name"],
            "navigation_path": [],
            "acceptance_criteria": (
                f"This screen has NO outgoing edges in the graph. Verify whether this is "
                f"intentional (booking confirmation, success state, terminal modal) or "
                f"whether the user can get stuck here without a back/dismiss action. "
                f"FAIL if: no way to leave this screen by any user action."
            ),
            "branch_label": "Deeplink — dead-end screens",
        })

    # 3. Dangling leads_to_hint references — element points to a screen name that doesn't exist
    dangling = find_dangling_hints(screens)
    for d in dangling:
        suggestion_text = (
            f" Closest existing screen: '{d['suggestion']}'."
            if d.get("suggestion")
            else " No similar screen name found in the project."
        )
        cases.append({
            "title": (
                f"Verify navigation from '{d['element_label']}' on "
                f"{d['screen_name']} actually works"
            ),
            "target_screen_name": d["screen_name"],
            "navigation_path": [],
            "acceptance_criteria": (
                f"The '{d['element_label']}' element on {d['screen_name']} is hinted to "
                f"lead to '{d['hint']}', but no screen with that name exists in the project map.{suggestion_text} "
                f"Tap the element and verify where it actually leads. "
                f"FAIL if: it crashes, leads to a blank screen, or lands somewhere unexpected."
            ),
            "branch_label": "Deeplink — dangling references",
        })

    # 4. Unreachable screens (only if a home is specified)
    if home_screen_id is not None:
        unreachable = find_unreachable_screens(screens, edges, home_screen_id)
        for s in unreachable:
            if s["id"] == home_screen_id:
                continue
            cases.append({
                "title": f"Verify '{s.get('display_name') or s['name']}' has a path from home",
                "target_screen_name": s["name"],
                "navigation_path": [],
                "acceptance_criteria": (
                    f"This screen cannot be reached by following any edges from the home screen. "
                    f"Verify a user can navigate here through normal app interaction (or via deeplink). "
                    f"FAIL if: there's no documented or interactive path to this screen."
                ),
                "branch_label": "Deeplink — unreachable from home",
            })

    if not cases:
        cases.append({
            "title": "Graph integrity — no issues detected",
            "target_screen_name": screens[0]["name"],
            "navigation_path": [],
            "acceptance_criteria": (
                "Graph analysis found no orphan screens, dead-ends, dangling hints, or "
                "unreachable screens. This is a baseline pass — re-run after adding more "
                "screens or edges to catch new structural issues."
            ),
            "branch_label": "Deeplink — clean",
        })

    logger.info(
        f"[DeeplinkUtilityPlanner] {len(orphans)} orphans, {len(dead_ends)} dead-ends, "
        f"{len(dangling)} dangling hints → {len(cases)} cases"
    )
    return cases
