"""Functional flow planner — verifies every interactive element on every screen.

For each screen with N tappable elements, emits ~N+1 cases:
- One case per element verifying its `leads_to_hint` destination
- One overall case verifying the screen has the expected number of elements

100% deterministic. Zero LLM calls. Walks the screens list and emits structured cases.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def generate_functional_flow_plan(
    screens: list[dict],
    edges: list[dict],
) -> list[dict]:
    """For each tappable element on each screen, generate a click-verification case.

    Args:
        screens: list of dicts with {id, name, display_name, elements}
        edges: list of dicts with {from_screen_id, to_screen_id, trigger}

    Returns:
        list of test case dicts (canonical shape: title, target_screen_name,
        navigation_path, acceptance_criteria, branch_label)
    """
    if not screens:
        return []

    # Build a quick lookup of edges by source screen for context
    edges_from: dict[int, list[dict]] = {}
    for e in edges:
        edges_from.setdefault(e["from_screen_id"], []).append(e)

    cases: list[dict] = []
    for screen in screens:
        screen_name = screen.get("name", "unknown")
        display = screen.get("display_name") or screen_name
        elements = screen.get("elements") or []

        if not elements:
            cases.append(_screen_overview_case(
                screen_name, display,
                "No interactive elements were detected — verify whether this screen "
                "is intentionally read-only or if elements were missed during analysis."
            ))
            continue

        # One case per interactive element
        for el in elements:
            label = el.get("label") or "(unlabeled)"
            etype = el.get("type") or "element"
            hint = (el.get("leads_to_hint") or "").strip()

            if hint:
                title = f"Tap '{label}' on {display} → should open '{hint}'"
                criteria = (
                    f"Tap the {etype} labeled '{label}' on the {display} screen. "
                    f"After tapping, the app should navigate to a screen matching '{hint}'. "
                    f"FAIL if: nothing happens, app crashes, or the destination screen does not match."
                )
            else:
                title = f"Verify '{label}' {etype} on {display} is tappable and labelled correctly"
                criteria = (
                    f"On the {display} screen, locate the {etype} labelled '{label}'. "
                    f"It should be visible, enabled (not greyed out), and the label text should be readable. "
                    f"FAIL if: missing, unreadable, disabled, or label differs from '{label}'."
                )

            cases.append({
                "title": title,
                "target_screen_name": screen_name,
                "navigation_path": [],
                "acceptance_criteria": criteria,
                "branch_label": f"{display} — interactions",
            })

        # Add a coverage case
        n_with_hints = sum(1 for e in elements if (e.get("leads_to_hint") or "").strip())
        actual_outgoing = len(edges_from.get(screen["id"], []))
        if n_with_hints > actual_outgoing:
            cases.append({
                "title": f"Verify all interactive elements on {display} have working navigation",
                "target_screen_name": screen_name,
                "navigation_path": [],
                "acceptance_criteria": (
                    f"The {display} screen has {n_with_hints} elements with navigation hints "
                    f"but only {actual_outgoing} confirmed edge(s) in the project graph. "
                    f"Verify each interactive element actually leads somewhere when tapped."
                ),
                "branch_label": f"{display} — coverage",
            })

    logger.info(
        f"[FunctionalFlowPlanner] Generated {len(cases)} cases across {len(screens)} screens"
    )
    return cases


def _screen_overview_case(screen_name: str, display: str, criteria: str) -> dict:
    return {
        "title": f"Audit interactive elements on {display}",
        "target_screen_name": screen_name,
        "navigation_path": [],
        "acceptance_criteria": criteria,
        "branch_label": f"{display} — coverage",
    }
