"""
agent/figma_journey_parser.py — Figma-first UAT: parse a Figma file into a journey spec

Connects to the Figma REST API, walks the document tree, classifies frames,
exports screen images, enriches screens with Claude intelligence, and produces
a structured JourneySpec dict that drives FigmaUATRunner.

Figma API docs:
    GET  https://api.figma.com/v1/files/{file_id}
    GET  https://api.figma.com/v1/images/{file_id}?ids=...&format=png&scale=2
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import uuid
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import requests as _requests
    _REQUESTS_AVAILABLE = True
except ImportError:
    _REQUESTS_AVAILABLE = False
    logger.warning("[FigmaJourneyParser] 'requests' not installed — Figma API calls will fail")

_FIGMA_API_BASE = "https://api.figma.com/v1"

# ---------------------------------------------------------------------------
# Keywords for classification
# ---------------------------------------------------------------------------

_SHEET_KEYWORDS = {
    "sheet", "drawer", "bottom", "modal", "popup", "overlay",
    "dialog", "alert", "snackbar", "toast",
}

_PERSUASION_KEYWORDS = {
    "persuasion", "nudge", "urgency", "limited", "offer", "deal",
    "only", "left", "people viewing", "social proof", "popular",
    "trending", "selling fast", "last", "hurry", "exclusive",
    "discount", "save", "off", "cashback", "coupon",
}

_COMPONENT_PREFIXES = ("_", ".", "component/", "components/", ".component", "#")


# ---------------------------------------------------------------------------
# FigmaJourneyParser
# ---------------------------------------------------------------------------


class FigmaJourneyParser:
    """
    Parses a Figma file to extract the full user journey as a structured spec.
    Uses Figma REST API: https://api.figma.com/v1/
    """

    def __init__(self, file_id: str, token: str = None) -> None:
        self.file_id = file_id
        self.token = (
            token
            or os.getenv("FIGMA_API_TOKEN")
            or os.getenv("FIGMA_ACCESS_TOKEN")
            or ""
        )
        if not self.token:
            logger.warning(
                "[FigmaJourneyParser] No Figma token found in FIGMA_API_TOKEN or "
                "FIGMA_ACCESS_TOKEN env vars. API calls will fail."
            )

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    @staticmethod
    def file_id_from_url(figma_url: str) -> str:
        """
        Extract file ID from Figma URL.
        Handles:
          https://www.figma.com/design/FILEID/name
          https://www.figma.com/file/FILEID/name
          https://www.figma.com/proto/FILEID/name
        Returns the FILEID portion.
        """
        pattern = r"figma\.com/(?:design|file|proto)/([A-Za-z0-9_-]+)"
        match = re.search(pattern, figma_url)
        if match:
            return match.group(1)
        raise ValueError(
            f"Could not extract file ID from Figma URL: {figma_url!r}. "
            "Expected format: https://www.figma.com/design/<FILE_ID>/name"
        )

    # ------------------------------------------------------------------
    # Main parse entry point
    # ------------------------------------------------------------------

    def parse(self, enrich: bool = True) -> dict:
        """
        Full parse of Figma file. Returns a JourneySpec dict.

        Steps:
        1. Fetch file document from Figma REST API
        2. Walk document tree: Document → Pages → Frames
        3. Classify each frame (main_screen / sheet / persuasion / modal / component)
        4. Extract text content and components from each frame
        5. Export frame images via Figma Images API
        6. Enrich screens with Claude intelligence (purpose, nav hints, test assertions)
        7. Generate test cases
        8. Return structured JourneySpec
        """
        logger.info(f"[FigmaJourneyParser] Parsing file: {self.file_id}")

        if not _REQUESTS_AVAILABLE:
            raise RuntimeError("'requests' library is required. Install it: pip install requests")

        # Step 1 — Fetch the Figma file
        file_data = self._fetch_file()
        file_name = file_data.get("name", "Untitled")
        document = file_data.get("document", {})

        logger.info(f"[FigmaJourneyParser] File name: {file_name!r}")

        # Step 2 & 3 — Walk pages and collect frames
        pages_result: list[dict] = []
        all_screens: list[dict] = []

        for page_node in document.get("children", []):
            if page_node.get("type") != "CANVAS":
                continue

            page_name = page_node.get("name", "Page")
            page_screens: list[dict] = []

            for seq, frame_node in enumerate(page_node.get("children", []), start=1):
                if frame_node.get("type") != "FRAME":
                    continue

                frame_name = frame_node.get("name", "")
                frame_id = frame_node.get("id", "")

                # Step 4 — Extract text + components
                text_content = self._extract_text_content(frame_node)
                components = self._extract_components(frame_node)

                # Step 3 — Classify
                frame_type = self._classify_frame({
                    "name": frame_name,
                    "text_content": text_content,
                    "components": components,
                })

                if frame_type == "component":
                    logger.debug(
                        f"[FigmaJourneyParser] Skipping component frame: {frame_name!r}"
                    )
                    continue

                screen: dict = {
                    "node_id": frame_id,
                    "name": frame_name,
                    "page_name": page_name,
                    "sequence": seq,
                    "type": frame_type,
                    "text_content": text_content,
                    "components": components,
                    "image_url": "",           # filled in step 5
                    "screen_purpose": "",       # filled in step 6
                    "navigation_steps": [],
                    "key_elements": [],
                    "test_assertions": [],
                }
                page_screens.append(screen)
                all_screens.append(screen)

            pages_result.append({"name": page_name, "screens": page_screens})
            logger.info(
                f"[FigmaJourneyParser] Page '{page_name}': {len(page_screens)} screens"
            )

        logger.info(f"[FigmaJourneyParser] Total screens found: {len(all_screens)}")

        # Step 5 — Export frame images (batched; Figma allows comma-separated IDs)
        if all_screens:
            node_ids = [s["node_id"] for s in all_screens]
            image_urls = self._export_frame_images(node_ids)
            for screen in all_screens:
                screen["image_url"] = image_urls.get(screen["node_id"], "")

        # Step 6 — Claude intelligence enrichment (skip when caller passes enrich=False
        # to save LLM quota when only frame metadata + image URLs are needed)
        if all_screens and enrich:
            all_screens = self._generate_screen_intelligence(all_screens)
            # Propagate enriched screens back into pages
            screen_index = {s["node_id"]: s for s in all_screens}
            for page in pages_result:
                page["screens"] = [
                    screen_index.get(s["node_id"], s) for s in page["screens"]
                ]

        # Categorise
        main_screens = [s for s in all_screens if s["type"] == "main_screen"]
        sheet_screens = [s for s in all_screens if s["type"] in ("sheet", "modal")]
        persuasion_screens = [s for s in all_screens if s["type"] == "persuasion"]

        # Step 7 — Generate test cases
        journey_spec: dict = {
            "file_id": self.file_id,
            "file_name": file_name,
            "pages": pages_result,
            "all_screens": all_screens,
            "persuasion_screens": persuasion_screens,
            "sheet_screens": sheet_screens,
            "main_screens": main_screens,
            "total_screens": len(all_screens),
            "test_cases": [],
            "parsed_at": datetime.utcnow().isoformat() + "Z",
        }
        journey_spec["test_cases"] = self.generate_test_cases(journey_spec)

        logger.info(
            f"[FigmaJourneyParser] Parse complete: "
            f"{len(all_screens)} screens, {len(journey_spec['test_cases'])} test cases"
        )
        return journey_spec

    # ------------------------------------------------------------------
    # Frame classification
    # ------------------------------------------------------------------

    def _classify_frame(self, frame: dict) -> str:
        """
        Classify a Figma frame based on name + content.

        Returns one of: 'main_screen' | 'sheet' | 'persuasion' | 'modal' | 'component'
        """
        name_lower = frame.get("name", "").lower()

        # Skip components/libraries
        for prefix in _COMPONENT_PREFIXES:
            if name_lower.startswith(prefix):
                return "component"

        # Check for sheet/modal/overlay keywords
        for kw in _SHEET_KEYWORDS:
            if kw in name_lower:
                if kw in ("modal", "dialog", "alert"):
                    return "modal"
                return "sheet"

        # Check text content for persuasion signals
        all_text = " ".join(
            t.get("text", "").lower()
            for t in frame.get("text_content", [])
        )
        all_text += " " + " ".join(frame.get("components", [])).lower()

        for kw in _PERSUASION_KEYWORDS:
            if kw in all_text or kw in name_lower:
                return "persuasion"

        return "main_screen"

    # ------------------------------------------------------------------
    # Text extraction
    # ------------------------------------------------------------------

    def _extract_text_content(self, node: dict, depth: int = 0) -> list[dict]:
        """
        Recursively extract all TEXT nodes from a Figma node.
        Returns list of {"text": str, "style": str, "importance": str}.
        """
        results: list[dict] = []

        if node.get("type") == "TEXT":
            raw_text = node.get("characters", "").strip()
            if not raw_text:
                return results

            # Determine importance from style / parent context
            style = node.get("style", {})
            font_size = style.get("fontSize", 14)
            font_weight = style.get("fontWeight", 400)
            node_name = node.get("name", "").lower()

            if any(kw in node_name for kw in ("button", "cta", "action", "submit", "book", "pay")):
                importance = "critical"
            elif font_size >= 20 or font_weight >= 700:
                importance = "high"
            else:
                importance = "medium"

            results.append({
                "text": raw_text,
                "style": f"size={font_size},weight={font_weight}",
                "importance": importance,
            })
            return results

        # Recurse into children (limit depth to avoid infinite loops in complex files)
        if depth < 10:
            for child in node.get("children", []):
                results.extend(self._extract_text_content(child, depth + 1))

        return results

    # ------------------------------------------------------------------
    # Component extraction
    # ------------------------------------------------------------------

    def _extract_components(self, node: dict, depth: int = 0) -> list[str]:
        """Extract component names used in this frame."""
        names: list[str] = []

        node_type = node.get("type", "")
        if node_type in ("COMPONENT", "INSTANCE"):
            comp_name = node.get("name", "")
            if comp_name:
                names.append(comp_name)

        if depth < 8:
            for child in node.get("children", []):
                names.extend(self._extract_components(child, depth + 1))

        return list(dict.fromkeys(names))  # deduplicate preserving order

    # ------------------------------------------------------------------
    # Figma Images API
    # ------------------------------------------------------------------

    def _export_frame_images(self, node_ids: list[str]) -> dict[str, str]:
        """
        Export Figma frames as PNG images via the Figma Images API.
        Batches up to 100 node IDs per request to stay within API limits.
        Returns {node_id: image_url} dict.
        """
        if not node_ids:
            return {}

        headers = {"X-Figma-Token": self.token}
        result: dict[str, str] = {}

        # Figma IDs use ':' but the API requires them URL-encoded as '%3A' — use comma-separated
        batch_size = 100
        for i in range(0, len(node_ids), batch_size):
            batch = node_ids[i : i + batch_size]
            ids_param = ",".join(batch)
            url = f"{_FIGMA_API_BASE}/images/{self.file_id}"
            params = {"ids": ids_param, "format": "png", "scale": "2"}

            try:
                resp = _requests.get(url, params=params, headers=headers, timeout=60)
                resp.raise_for_status()
                data = resp.json()

                if data.get("err"):
                    logger.error(
                        f"[FigmaJourneyParser] Images API error: {data['err']}"
                    )
                    continue

                images = data.get("images", {})
                result.update(images)
                logger.info(
                    f"[FigmaJourneyParser] Exported {len(images)} image URLs "
                    f"(batch {i // batch_size + 1})"
                )

            except Exception as exc:
                logger.error(
                    f"[FigmaJourneyParser] Failed to export images (batch {i // batch_size + 1}): {exc}"
                )

            # Brief pause between batches to be polite to the API
            if i + batch_size < len(node_ids):
                time.sleep(0.5)

        return result

    # ------------------------------------------------------------------
    # Claude intelligence enrichment
    # ------------------------------------------------------------------

    def _generate_screen_intelligence(self, screens: list[dict]) -> list[dict]:
        """
        Send all screens (names + text content) to Claude in ONE batched call.
        Enriches each screen with: screen_purpose, navigation_steps, key_elements,
        test_assertions.
        Returns the enriched screen list.
        """
        try:
            from utils.claude_client import ask  # noqa: PLC0415
        except ImportError:
            logger.warning(
                "[FigmaJourneyParser] claude_client not importable — skipping AI enrichment"
            )
            return screens

        # Build a compact summary of all screens for the prompt
        screen_summaries: list[dict] = []
        for i, s in enumerate(screens):
            texts = [t["text"] for t in s["text_content"][:15]]  # cap per screen
            screen_summaries.append({
                "index": i,
                "name": s["name"],
                "type": s["type"],
                "page": s["page_name"],
                "texts": texts,
                "components": s["components"][:10],
            })

        prompt = (
            "You are a senior mobile QA engineer analysing a MakeMyTrip Android app Figma design.\n\n"
            "Below is a list of screens extracted from a Figma file. For each screen, provide:\n"
            "  - screen_purpose: 1-sentence description of what this screen does in the user journey\n"
            "  - navigation_steps: ordered list of actions a tester should take to reach this screen "
            "in the MakeMyTrip Android app (be specific: e.g. 'Tap Hotels on home screen')\n"
            "  - key_elements: list of critical UI element labels/texts to verify are present\n"
            "  - test_assertions: specific, checkable assertions "
            "(e.g. 'CTA text says Book Now', 'Price shown in INR', 'Discount badge visible')\n\n"
            "Return a JSON array with one object per screen, keyed by 'index'. Example:\n"
            '[{"index": 0, "screen_purpose": "...", "navigation_steps": ["step 1", ...], '
            '"key_elements": ["element 1", ...], "test_assertions": ["assertion 1", ...]}, ...]\n\n'
            "SCREENS:\n"
            + json.dumps(screen_summaries, indent=2)
        )

        try:
            raw = ask(
                prompt=prompt,
                system=(
                    "You are a mobile QA expert. Return ONLY valid JSON — no markdown, no prose."
                ),
                max_tokens=8192,
            )

            # Strip code fences if present
            if raw.strip().startswith("```"):
                raw = raw.strip().split("\n", 1)[-1].rsplit("```", 1)[0].strip()

            enriched_list: list[dict] = json.loads(raw)
            enrichment_index: dict[int, dict] = {e["index"]: e for e in enriched_list}

            for i, screen in enumerate(screens):
                enrichment = enrichment_index.get(i, {})
                screen["screen_purpose"] = enrichment.get("screen_purpose", "")
                screen["navigation_steps"] = enrichment.get("navigation_steps", [])
                screen["key_elements"] = enrichment.get("key_elements", [])
                screen["test_assertions"] = enrichment.get("test_assertions", [])

            logger.info(
                f"[FigmaJourneyParser] Claude enriched {len(enrichment_index)} screens"
            )
        except (json.JSONDecodeError, KeyError, Exception) as exc:
            logger.warning(
                f"[FigmaJourneyParser] Screen intelligence enrichment failed: {exc}. "
                "Screens will have empty purpose/navigation fields."
            )

        return screens

    # ------------------------------------------------------------------
    # Test case generation
    # ------------------------------------------------------------------

    def generate_test_cases(self, journey_spec: dict) -> list[dict]:
        """
        Generate test cases from the journey spec:
        - One 'screen_compliance' test per main screen (navigate → screenshot → compare)
        - One 'screen_compliance' test per sheet/modal (trigger → screenshot → compare)
        - One 'element_check' test per persuasion screen (check persuasion visible)
        - One 'flow' test covering the full sequential journey

        Each test case:
        {
          "id": str,
          "name": str,
          "type": "screen_compliance" | "element_check" | "flow",
          "figma_node_id": str,
          "figma_screen_name": str,
          "navigation_steps": [str],
          "assertions": [{"check": str, "element": str, "expected": str}],
          "severity": "critical" | "high" | "medium"
        }
        """
        test_cases: list[dict] = []

        # --- Per main screen ---
        for screen in journey_spec.get("main_screens", []):
            tc_id = f"tc_screen_{uuid.uuid4().hex[:6]}"
            assertions = [
                {"check": a, "element": "", "expected": "present"}
                for a in screen.get("test_assertions", [])
            ]
            test_cases.append({
                "id": tc_id,
                "name": f"Screen compliance: {screen['name']}",
                "type": "screen_compliance",
                "figma_node_id": screen["node_id"],
                "figma_screen_name": screen["name"],
                "figma_image_url": screen.get("image_url", ""),
                "navigation_steps": screen.get("navigation_steps", []),
                "assertions": assertions,
                "severity": "critical" if screen.get("sequence", 99) <= 3 else "high",
            })

        # --- Per sheet / modal ---
        for screen in journey_spec.get("sheet_screens", []):
            tc_id = f"tc_sheet_{uuid.uuid4().hex[:6]}"
            assertions = [
                {"check": a, "element": "", "expected": "present"}
                for a in screen.get("test_assertions", [])
            ]
            test_cases.append({
                "id": tc_id,
                "name": f"Sheet compliance: {screen['name']}",
                "type": "screen_compliance",
                "figma_node_id": screen["node_id"],
                "figma_screen_name": screen["name"],
                "figma_image_url": screen.get("image_url", ""),
                "navigation_steps": screen.get("navigation_steps", []),
                "assertions": assertions,
                "severity": "high",
            })

        # --- Per persuasion screen ---
        for screen in journey_spec.get("persuasion_screens", []):
            tc_id = f"tc_nudge_{uuid.uuid4().hex[:6]}"
            # Build persuasion-specific assertions from high-importance text
            critical_texts = [
                t["text"] for t in screen.get("text_content", [])
                if t.get("importance") in ("critical", "high")
            ]
            assertions = [
                {"check": f"Persuasion text visible", "element": t, "expected": "present"}
                for t in critical_texts[:5]
            ]
            if not assertions:
                assertions = [
                    {"check": "Persuasion element visible", "element": screen["name"], "expected": "present"}
                ]
            test_cases.append({
                "id": tc_id,
                "name": f"Persuasion check: {screen['name']}",
                "type": "element_check",
                "figma_node_id": screen["node_id"],
                "figma_screen_name": screen["name"],
                "figma_image_url": screen.get("image_url", ""),
                "navigation_steps": screen.get("navigation_steps", []),
                "assertions": assertions,
                "severity": "critical",
            })

        # --- Full flow test (link all main screens in sequence) ---
        main_screens = journey_spec.get("main_screens", [])
        if len(main_screens) >= 2:
            flow_steps: list[str] = []
            for s in main_screens:
                flow_steps.extend(s.get("navigation_steps", []))
            # Deduplicate consecutive identical steps
            deduped: list[str] = []
            for step in flow_steps:
                if not deduped or deduped[-1] != step:
                    deduped.append(step)

            test_cases.append({
                "id": f"tc_flow_{uuid.uuid4().hex[:6]}",
                "name": "End-to-end flow: full user journey",
                "type": "flow",
                "figma_node_id": "",
                "figma_screen_name": "FULL_FLOW",
                "figma_image_url": "",
                "navigation_steps": deduped[:30],  # cap at 30 steps
                "assertions": [
                    {
                        "check": f"Screen reached: {s['name']}",
                        "element": s["name"],
                        "expected": "navigable",
                    }
                    for s in main_screens
                ],
                "severity": "critical",
            })

        logger.info(
            f"[FigmaJourneyParser] Generated {len(test_cases)} test cases"
        )
        return test_cases

    # ------------------------------------------------------------------
    # Figma file fetch
    # ------------------------------------------------------------------

    def _fetch_file(self) -> dict:
        """Fetch the full Figma file document. Raises on error."""
        url = f"{_FIGMA_API_BASE}/files/{self.file_id}"
        headers = {"X-Figma-Token": self.token}
        # depth=2 keeps API cost ~4x lower than depth=4. Figma free tier charges by
        # "compute cost" per request (not request count), so deep queries burn the
        # monthly quota fast. We get page → frame metadata at depth=2, which is
        # enough for screen-level mapping. Use depth=3 only if you need per-element
        # text content for the enrichment pass.
        params = {"depth": "2"}

        try:
            resp = _requests.get(url, headers=headers, params=params, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            if "err" in data:
                raise RuntimeError(f"Figma API returned error: {data['err']}")
            return data
        except Exception as exc:
            logger.error(f"[FigmaJourneyParser] Failed to fetch Figma file: {exc}")
            raise
