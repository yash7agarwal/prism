"""Figma-aware test plan generator.

When a project has a Figma file, this planner generates test cases that compare
the implemented app screens to the design source of truth — covering content,
typography, color, persuasion/nudges, layout, and edge cases.

Workflow:
1. Parse the Figma file → list of frames with image URLs
2. For each substantive frame, find the best-matching project screen by name
3. Download both images, compose them side-by-side
4. Send composite to vision LLM with a design-fidelity prompt
5. Aggregate per-frame cases into one plan

Reuses utils.claude_client.ask_vision (which routes to Gemini if LLM_PROVIDER=gemini)
and agent.figma_journey_parser for Figma API access.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import time

import httpx
from PIL import Image

from utils.claude_client import DEFAULT_MODEL, ask_vision

logger = logging.getLogger(__name__)


_DESIGN_FIDELITY_PROMPT = """\
You are a senior UX/QA engineer reviewing whether an app screen matches its Figma design specification.

You see ONE composite image:
- LEFT half: the FIGMA design frame ("{frame_name}") — the source of truth
- RIGHT half: the actual APP screen ("{screen_name}") — what was built

Generate UAT test cases that, if they all pass, would prove the app screen faithfully matches the design.

CATEGORIES TO COVER (aim for 2-4 cases per category that has visible elements):
1. CONTENT — every text, label, copy, microcopy. Compare verbatim. Check for missing text.
2. PERSUASION & NUDGES — badges, banners, callouts, social proof, urgency cues, "Most Booked", trust signals. Verify each one's presence + copy.
3. TYPOGRAPHY — font weight, relative size, color of headings, labels, prices. (Use what you can visually infer.)
4. COLOR — brand colors, CTA colors, backgrounds, borders, status colors.
5. LAYOUT — card spacing, alignment, ordering, grouping, grid structure, padding.
6. ICONS & IMAGERY — icons present and matching, image aspect ratios, gallery dots, hero placement.
7. INTERACTIVE STATES — selected vs unselected tabs, primary vs secondary buttons, disabled states.
8. EDGE CASES — what should happen with long names, missing fields, error states, empty states.

Output ONLY a JSON object (no markdown, no prose):
{{
  "cases": [
    {{
      "title": "<concise title — what is being verified>",
      "category": "content|persuasion|typography|color|layout|icons|states|edge",
      "design_expected": "<what the design shows, verbatim where possible>",
      "what_to_verify": "<the specific check the QA engineer should perform>",
      "fail_criteria": "<what would constitute a failure>"
    }}
  ]
}}

RULES:
- Be SPECIFIC. Reference actual elements you see in the design ("the orange BOOK NOW button", "the 'Most Booked' badge top-right of the photo").
- Generate 8-20 cases per screen. Quality > quantity.
- If you see something in the design that's MISSING in the app, generate a case for it.
- If you see something in the app that's NOT in the design, generate a case for it.
- For typography/color you can't measure exactly, write subjective checks ("Verify hotel name is bolder and larger than the location text").
"""


def generate_figma_test_plan(
    feature_description: str,
    figma_file_id: str,
    screens: list[dict],
    figma_token: str | None = None,
) -> list[dict]:
    """Generate design-fidelity test cases by comparing Figma frames to project screens.

    Args:
        feature_description: The feature being UAT'd (used to filter relevant frames)
        figma_file_id: Figma file ID (e.g., 'rid4WC0zcs0yt3RjpST0dx')
        screens: list of {id, name, display_name, screenshot_path} from the project
        figma_token: Figma API token (defaults to env var)

    Returns:
        list of test case dicts with title, target_screen_name, navigation_path,
        acceptance_criteria, branch_label
    """
    token = figma_token or os.environ.get("FIGMA_ACCESS_TOKEN") or os.environ.get("FIGMA_API_TOKEN")
    if not token:
        logger.warning("[FigmaTestPlanner] No Figma token, skipping")
        return []

    # 1. Parse Figma file → list of frames (skip Claude enrichment to save quota —
    # we only need frame name + image URL)
    try:
        from agent.figma_journey_parser import FigmaJourneyParser
        parser = FigmaJourneyParser(figma_file_id, token=token)
        journey = parser.parse(enrich=False)
    except Exception as exc:
        logger.error(f"[FigmaTestPlanner] Failed to parse Figma file: {exc}")
        return []

    figma_frames = journey.get("all_screens", [])
    if not figma_frames:
        logger.warning("[FigmaTestPlanner] No frames in Figma file")
        return []

    # 2. Filter to substantive frames (skip tiny icons / fragments)
    substantive_frames = _filter_substantive_frames(figma_frames)
    logger.info(
        f"[FigmaTestPlanner] {len(figma_frames)} frames in file, {len(substantive_frames)} substantive"
    )

    # 3. For each substantive frame, generate fidelity cases
    all_cases: list[dict] = []
    for frame in substantive_frames:
        frame_name = frame.get("name", "Unknown")
        frame_image_url = frame.get("image_url")
        if not frame_image_url:
            continue

        # Download Figma frame image
        try:
            figma_bytes = _download(frame_image_url)
        except Exception as exc:
            logger.warning(f"[FigmaTestPlanner] Could not download frame '{frame_name}': {exc}")
            continue

        # Find the best matching project screen
        target_screen = _find_best_match(frame_name, feature_description, screens)
        if not target_screen:
            logger.warning(f"[FigmaTestPlanner] No matching app screen for frame '{frame_name}'")
            continue

        screen_path = target_screen.get("screenshot_path")
        if not screen_path or not os.path.exists(screen_path):
            continue
        with open(screen_path, "rb") as fh:
            app_bytes = fh.read()

        # Compose side-by-side and call vision LLM
        try:
            composite = _compose_side_by_side(figma_bytes, app_bytes)
        except Exception as exc:
            logger.warning(f"[FigmaTestPlanner] Could not compose images for '{frame_name}': {exc}")
            continue

        prompt = _DESIGN_FIDELITY_PROMPT.format(
            frame_name=frame_name,
            screen_name=target_screen.get("display_name") or target_screen.get("name"),
        )
        try:
            raw = ask_vision(
                prompt=prompt,
                image_bytes=composite,
                media_type="image/png",
                model=DEFAULT_MODEL,
                max_tokens=4096,
            )
            parsed = _parse_json(raw)
            cases = parsed.get("cases", [])
        except Exception as exc:
            logger.warning(f"[FigmaTestPlanner] Vision call failed for '{frame_name}': {exc}")
            continue

        # Convert into the canonical case shape used by routes/plans.py
        for c in cases:
            all_cases.append({
                "title": c.get("title", "Untitled"),
                "target_screen_name": target_screen.get("name"),
                "navigation_path": [],
                "acceptance_criteria": _format_criteria(c),
                "branch_label": f"Figma: {frame_name} — {c.get('category', 'fidelity')}",
            })

        logger.info(
            f"[FigmaTestPlanner] Frame '{frame_name}' → screen '{target_screen.get('name')}': {len(cases)} cases"
        )

        # Throttle to stay under Gemini free tier's 15 RPM cap
        time.sleep(5)

    return all_cases


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _filter_substantive_frames(frames: list[dict]) -> list[dict]:
    """Skip frames whose names suggest they are tiny icons or fragments."""
    skip_patterns = re.compile(r"\b(icon|location|frame\s*\d{6,}|component|symbol|ic_)", re.I)
    out = []
    for f in frames:
        name = (f.get("name") or "").strip()
        if not name:
            continue
        if skip_patterns.search(name):
            continue
        out.append(f)
    # If filter removed everything, fall back to all frames (better than zero output)
    return out or frames


def _normalize(s: str) -> set[str]:
    """Normalize a name into lowercase tokens for matching."""
    s = re.sub(r"[^a-zA-Z0-9 ]", " ", s or "").lower()
    return set(t for t in s.split() if len(t) > 2)


def _find_best_match(
    frame_name: str,
    feature_description: str,
    screens: list[dict],
) -> dict | None:
    """Match a Figma frame to the best app screen by name token overlap.

    Falls back to feature description tokens if frame name has no match.
    """
    if not screens:
        return None

    frame_tokens = _normalize(frame_name) | _normalize(feature_description)
    best, best_score = None, 0
    for s in screens:
        screen_tokens = _normalize(s.get("name", "") + " " + (s.get("display_name") or ""))
        score = len(frame_tokens & screen_tokens)
        if score > best_score:
            best_score, best = score, s

    # If even the best match has zero overlap, return the first hotel-detail-ish screen
    if best_score == 0:
        for s in screens:
            name = (s.get("name") or "").lower()
            if any(kw in name for kw in ["detail", "hotel"]):
                return s
        return screens[0]
    return best


def _download(url: str, timeout: float = 30) -> bytes:
    r = httpx.get(url, timeout=timeout)
    r.raise_for_status()
    return r.content


def _compose_side_by_side(left_bytes: bytes, right_bytes: bytes) -> bytes:
    """Place two images side-by-side as a single PNG. Resizes both to same height."""
    left = Image.open(io.BytesIO(left_bytes)).convert("RGB")
    right = Image.open(io.BytesIO(right_bytes)).convert("RGB")

    # Cap height for token efficiency — vision models don't need >1200px
    target_h = min(1200, max(left.height, right.height))
    if left.height != target_h:
        left = left.resize((int(left.width * target_h / left.height), target_h))
    if right.height != target_h:
        right = right.resize((int(right.width * target_h / right.height), target_h))

    gap = 20
    composite = Image.new("RGB", (left.width + right.width + gap, target_h), "white")
    composite.paste(left, (0, 0))
    composite.paste(right, (left.width + gap, 0))

    out = io.BytesIO()
    composite.save(out, format="PNG", optimize=True)
    return out.getvalue()


def _format_criteria(case: dict) -> str:
    """Build a single-string acceptance criteria from the design-fidelity case fields."""
    parts = []
    if case.get("what_to_verify"):
        parts.append(case["what_to_verify"])
    if case.get("design_expected"):
        parts.append(f"(Design: {case['design_expected']})")
    if case.get("fail_criteria"):
        parts.append(f"FAIL if: {case['fail_criteria']}")
    return " ".join(parts) or "Match the design specification."


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
