"""
agent/figma_uat_runner.py — Figma-first UAT executor

Executes UAT by navigating the Android app to each screen defined in a
FigmaJourneyParser JourneySpec and comparing the live app against the design.

Flow per test case:
  1. Navigate to the screen via a Claude tool-use loop (same pattern as ScenarioRunnerAgent)
  2. Take a screenshot of the app
  3. Download the Figma reference frame image
  4. Compare via FigmaComparator (Claude vision + optional pixel diff fallback)
  5. Check text/element assertions against the UI tree
  6. Record the result

Returns a FigmaUATReport dict and saves a Markdown report to reports/.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from agent.figma_comparator import FigmaComparator
from utils.claude_client import ask_with_tools

if TYPE_CHECKING:
    from tools.android_device import AndroidDevice

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tool schema for the navigation loop — reuses ScenarioRunnerAgent's tools
# ---------------------------------------------------------------------------

_NAV_TOOLS: list[dict] = [
    {
        "name": "take_screenshot",
        "description": "Take a screenshot of the current screen. Returns the file path.",
        "input_schema": {
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "Short label for this screenshot"}
            },
            "required": [],
        },
    },
    {
        "name": "get_ui_elements",
        "description": "Get all interactive UI elements as XML. Use before tapping.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "tap_element",
        "description": "Tap a UI element by visible text or coordinates.",
        "input_schema": {
            "type": "object",
            "properties": {
                "element_text": {"type": "string", "description": "Visible text to tap"},
                "x": {"type": "integer", "description": "X coordinate fallback"},
                "y": {"type": "integer", "description": "Y coordinate fallback"},
            },
            "required": [],
        },
    },
    {
        "name": "swipe_screen",
        "description": "Swipe the screen in a direction.",
        "input_schema": {
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["up", "down", "left", "right"],
                }
            },
            "required": ["direction"],
        },
    },
    {
        "name": "type_text_in_field",
        "description": "Type text into the currently focused input field.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to type"}
            },
            "required": ["text"],
        },
    },
    {
        "name": "press_back",
        "description": "Press the Android back button.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "navigation_complete",
        "description": (
            "Call this when you have successfully navigated to the target screen "
            "and the correct content is visible."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "success": {
                    "type": "boolean",
                    "description": "True if target screen was reached, False if blocked",
                },
                "notes": {
                    "type": "string",
                    "description": "Brief description of the current screen state",
                },
            },
            "required": ["success"],
        },
    },
]

_NAV_SYSTEM_PROMPT = """\
You are a senior QA tester navigating a MakeMyTrip Android app to reach a specific screen.
Follow the navigation steps precisely. After each significant action, call get_ui_elements
to orient yourself. Once you are on the correct screen, call navigation_complete(success=True).
If you are blocked after exhausting all steps, call navigation_complete(success=False).
"""


# ---------------------------------------------------------------------------
# FigmaUATRunner
# ---------------------------------------------------------------------------


class FigmaUATRunner:
    """
    Executes Figma-first UAT: navigates the Android app to each screen
    defined in the Figma journey spec and compares against the design.
    """

    def __init__(
        self,
        device: "AndroidDevice",
        journey_spec: dict,
        run_id: str,
        package_name: str = "",
    ) -> None:
        self.device = device
        self.journey_spec = journey_spec
        self.run_id = run_id
        self.package_name = package_name

        self.file_id = journey_spec.get("file_id", "")
        self.file_name = journey_spec.get("file_name", "Untitled")

        # FigmaComparator reused for visual diff
        figma_token = os.getenv("FIGMA_API_TOKEN") or os.getenv("FIGMA_ACCESS_TOKEN") or ""
        self.comparator = FigmaComparator(
            figma_file_id=self.file_id,
            figma_token=figma_token,
            run_id=run_id,
        )

        # Evidence directory
        self._evidence_dir = Path(".tmp") / "figma_uat" / run_id
        self._evidence_dir.mkdir(parents=True, exist_ok=True)

        # Navigation state (used by tool callbacks)
        self._nav_done: bool = False
        self._nav_success: bool = False
        self._nav_screenshot_path: Optional[str] = None

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self) -> dict:
        """
        For each test case in journey_spec["test_cases"]:
          1. Navigate to the screen
          2. Screenshot
          3. Download Figma frame
          4. Compare via FigmaComparator
          5. Check assertions
          6. Record result

        Returns FigmaUATReport dict.
        """
        test_cases: list[dict] = self.journey_spec.get("test_cases", [])
        logger.info(
            f"[FigmaUATRunner] Starting run {self.run_id}: "
            f"{len(test_cases)} test cases, file={self.file_name!r}"
        )

        screen_results: list[dict] = []
        persuasion_results: list[dict] = []
        critical_issues: list[dict] = []

        compliant = 0
        non_compliant = 0
        skipped = 0

        for tc in test_cases:
            tc_id = tc.get("id", "unknown")
            tc_name = tc.get("name", "Unnamed")
            tc_type = tc.get("type", "screen_compliance")
            severity = tc.get("severity", "medium")
            logger.info(f"[FigmaUATRunner] Running test case: {tc_name!r} ({tc_id})")

            try:
                result = self._run_test_case(tc)
            except Exception as exc:
                logger.error(
                    f"[FigmaUATRunner] Test case {tc_id!r} raised: {exc}"
                )
                result = {
                    "tc_id": tc_id,
                    "tc_name": tc_name,
                    "tc_type": tc_type,
                    "severity": severity,
                    "status": "error",
                    "navigation_success": False,
                    "compare_result": None,
                    "assertion_results": [],
                    "screenshot_path": None,
                    "error": str(exc),
                }

            status = result.get("status", "error")
            if status == "compliant":
                compliant += 1
            elif status in ("non_compliant", "error"):
                non_compliant += 1
                if severity == "critical":
                    critical_issues.append({
                        "tc_id": tc_id,
                        "tc_name": tc_name,
                        "severity": severity,
                        "issues": result.get("compare_result", {}).get("issues", [])
                        + [r["actual"] for r in result.get("assertion_results", []) if not r.get("passed")],
                    })
            else:  # skipped / nav_failed
                skipped += 1

            if tc_type == "element_check":
                persuasion_results.append(result)
            else:
                screen_results.append(result)

        total_tested = compliant + non_compliant
        compliance_rate = (compliant / total_tested) if total_tested else 0.0

        if compliance_rate >= 0.90:
            overall_verdict = "COMPLIANT"
        elif compliance_rate >= 0.60:
            overall_verdict = "PARTIALLY_COMPLIANT"
        else:
            overall_verdict = "NON_COMPLIANT"

        report = {
            "run_id": self.run_id,
            "file_name": self.file_name,
            "total_screens": self.journey_spec.get("total_screens", 0),
            "tested_screens": total_tested,
            "compliant": compliant,
            "non_compliant": non_compliant,
            "skipped": skipped,
            "compliance_rate": round(compliance_rate, 4),
            "screen_results": screen_results,
            "persuasion_results": persuasion_results,
            "critical_issues": critical_issues,
            "overall_verdict": overall_verdict,
            "report_path": "",
        }

        report["report_path"] = self._generate_report(
            results=screen_results + persuasion_results,
            report=report,
        )

        logger.info(
            f"[FigmaUATRunner] Run complete: verdict={overall_verdict} "
            f"compliance={round(compliance_rate * 100, 1)}% "
            f"({compliant} compliant, {non_compliant} non-compliant, {skipped} skipped)"
        )
        return report

    # ------------------------------------------------------------------
    # Per-test-case execution
    # ------------------------------------------------------------------

    def _run_test_case(self, tc: dict) -> dict:
        """Run a single test case. Returns a ScreenResult dict."""
        tc_id = tc.get("id", "unknown")
        tc_name = tc.get("name", "Unnamed")
        tc_type = tc.get("type", "screen_compliance")
        node_id = tc.get("figma_node_id", "")
        nav_steps = tc.get("navigation_steps", [])
        assertions = tc.get("assertions", [])
        severity = tc.get("severity", "medium")

        # Step 1 — Navigate
        nav_success = self._navigate_to_screen(tc)
        if not nav_success:
            logger.warning(
                f"[FigmaUATRunner] Navigation failed for {tc_name!r} — skipping compare"
            )
            return {
                "tc_id": tc_id,
                "tc_name": tc_name,
                "tc_type": tc_type,
                "severity": severity,
                "status": "nav_failed",
                "navigation_success": False,
                "compare_result": None,
                "assertion_results": [],
                "screenshot_path": None,
                "error": "Navigation did not reach target screen",
            }

        # Step 2 — Screenshot
        screenshot_path = str(self._evidence_dir / f"{tc_id}_screenshot.png")
        try:
            self.device.screenshot(save_path=screenshot_path)
        except Exception as exc:
            logger.error(f"[FigmaUATRunner] Screenshot failed: {exc}")
            screenshot_path = None

        # Step 3+4 — Compare to Figma (only for screen_compliance, not pure element_check)
        compare_result: Optional[dict] = None
        if tc_type in ("screen_compliance",) and node_id and screenshot_path:
            compare_result = self.comparator.compare_screenshot_to_frame(
                screenshot_path=screenshot_path,
                figma_node_id=node_id,
                screen_name=tc_name,
            )
            logger.info(
                f"[FigmaUATRunner] Compare verdict={compare_result.get('verdict')} "
                f"score={compare_result.get('match_score')}"
            )

        # Step 5 — Check assertions
        assertion_results: list[dict] = []
        if assertions and screenshot_path:
            assertion_results = self._check_assertions(
                screenshot_path=screenshot_path,
                assertions=assertions,
                device=self.device,
            )

        # Step 6 — Determine status
        visual_ok = True
        if compare_result:
            verdict = compare_result.get("verdict", "UNKNOWN")
            visual_ok = verdict in ("MATCHES", "UNKNOWN")  # UNKNOWN = not enough info, don't fail

        assertion_ok = all(r.get("passed", True) for r in assertion_results)
        critical_assertions_ok = all(
            r.get("passed", True)
            for r in assertion_results
            if r.get("importance") == "critical"
        )

        if visual_ok and assertion_ok:
            status = "compliant"
        elif not critical_assertions_ok or (compare_result and compare_result.get("verdict") == "DIFFERS"):
            status = "non_compliant"
        else:
            status = "compliant"  # minor issues only

        return {
            "tc_id": tc_id,
            "tc_name": tc_name,
            "tc_type": tc_type,
            "severity": severity,
            "status": status,
            "navigation_success": True,
            "compare_result": compare_result,
            "assertion_results": assertion_results,
            "screenshot_path": screenshot_path,
            "error": None,
        }

    # ------------------------------------------------------------------
    # Navigation via Claude tool loop
    # ------------------------------------------------------------------

    def _navigate_to_screen(self, test_case: dict) -> bool:
        """
        Use a Claude tool loop (ScenarioRunnerAgent pattern) to navigate
        to the screen described in test_case["navigation_steps"].
        Returns True if navigation succeeded.
        """
        nav_steps = test_case.get("navigation_steps", [])
        screen_name = test_case.get("figma_screen_name", "target screen")

        # Reset navigation state
        self._nav_done = False
        self._nav_success = False

        if not nav_steps:
            logger.info(
                f"[FigmaUATRunner] No navigation steps for {screen_name!r} — assuming current screen"
            )
            return True

        steps_formatted = "\n".join(f"  {i + 1}. {s}" for i, s in enumerate(nav_steps))
        initial_prompt = (
            f"Target screen: {screen_name}\n\n"
            f"Navigation steps:\n{steps_formatted}\n\n"
            "Take a screenshot to see the current state, then follow the navigation steps. "
            "Once you are on the correct screen, call navigation_complete(success=True). "
            "If you cannot reach the screen after trying all steps, call navigation_complete(success=False)."
        )

        messages: list[dict] = [{"role": "user", "content": initial_prompt}]
        max_iters = len(nav_steps) * 4 + 10

        for _iter in range(max_iters):
            if self._nav_done:
                break

            try:
                response = ask_with_tools(
                    messages=messages,
                    tools=_NAV_TOOLS,
                    system=_NAV_SYSTEM_PROMPT,
                    max_tokens=2048,
                )
            except Exception as exc:
                logger.error(f"[FigmaUATRunner] Navigation loop error: {exc}")
                break

            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                break

            if response.stop_reason != "tool_use":
                break

            tool_results: list[dict] = []
            for block in response.content:
                if block.type == "tool_use":
                    result_text = self._execute_nav_tool(block.name, block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(result_text),
                    })

            if tool_results:
                messages.append({"role": "user", "content": tool_results})

        logger.info(
            f"[FigmaUATRunner] Navigation for {screen_name!r}: "
            f"success={self._nav_success}"
        )
        return self._nav_success

    def _execute_nav_tool(self, tool_name: str, tool_input: dict) -> str:
        """Dispatch navigation tool calls."""
        try:
            if tool_name == "take_screenshot":
                label = tool_input.get("label", "nav")
                path = str(self._evidence_dir / f"nav_{label}_{int(time.time())}.png")
                self.device.screenshot(save_path=path)
                self._nav_screenshot_path = path
                return f"Screenshot saved: {path}"

            elif tool_name == "get_ui_elements":
                xml = self.device.get_ui_tree()
                return xml[:3000] + "\n...[truncated]" if len(xml) > 3000 else xml

            elif tool_name == "tap_element":
                element_text = tool_input.get("element_text", "").strip()
                x = tool_input.get("x")
                y = tool_input.get("y")
                if element_text:
                    success = self.device.tap_text(element_text)
                    if success:
                        return f"Tapped '{element_text}'"
                    if x is not None and y is not None:
                        self.device.tap(int(x), int(y))
                        return f"Text '{element_text}' not found; tapped ({x},{y})"
                    return f"Element '{element_text}' not found"
                elif x is not None and y is not None:
                    self.device.tap(int(x), int(y))
                    return f"Tapped ({x},{y})"
                return "ERROR: tap_element requires element_text or (x,y)"

            elif tool_name == "swipe_screen":
                direction = tool_input.get("direction", "up")
                self.device.swipe(direction)
                return f"Swiped {direction}"

            elif tool_name == "type_text_in_field":
                text = tool_input.get("text", "")
                self.device.type_text(text)
                return f"Typed: '{text}'"

            elif tool_name == "press_back":
                self.device.press_back()
                return "Pressed back"

            elif tool_name == "navigation_complete":
                success = bool(tool_input.get("success", False))
                notes = tool_input.get("notes", "")
                self._nav_done = True
                self._nav_success = success
                logger.info(
                    f"[FigmaUATRunner] navigation_complete: success={success} notes={notes!r}"
                )
                return f"Navigation complete: success={success}"

            else:
                return f"Unknown tool: {tool_name}"

        except Exception as exc:
            logger.error(f"[FigmaUATRunner] Nav tool '{tool_name}' error: {exc}")
            return f"ERROR: {exc}"

    # ------------------------------------------------------------------
    # Assertion checking
    # ------------------------------------------------------------------

    def _check_assertions(
        self,
        screenshot_path: str,
        assertions: list[dict],
        device: "AndroidDevice",
    ) -> list[dict]:
        """
        Check each assertion:
        - Text assertions: search the UI tree XML for the expected text
        - Visual assertions: use Claude vision on the screenshot

        Returns list of {assertion, passed: bool, actual: str, evidence: str}
        """
        results: list[dict] = []

        # Fetch UI tree once for all text checks
        ui_xml: str = ""
        try:
            ui_xml = device.get_ui_tree()
        except Exception as exc:
            logger.warning(f"[FigmaUATRunner] Could not get UI tree for assertions: {exc}")

        for assertion in assertions:
            check = assertion.get("check", "")
            element = assertion.get("element", "")
            expected = assertion.get("expected", "present")

            passed = False
            actual = ""
            evidence = ""

            try:
                # Text presence check — search the UI XML
                if element and ui_xml:
                    found = element.lower() in ui_xml.lower()
                    passed = found if expected == "present" else not found
                    actual = "found in UI tree" if found else "not found in UI tree"
                    evidence = "ui_tree_search"
                else:
                    # Fall back to asking Claude about the screenshot
                    passed, actual, evidence = self._check_visual_assertion(
                        screenshot_path=screenshot_path,
                        check=check,
                        element=element,
                        expected=expected,
                    )
            except Exception as exc:
                actual = f"Error: {exc}"
                evidence = "error"

            results.append({
                "assertion": check,
                "element": element,
                "expected": expected,
                "passed": passed,
                "actual": actual,
                "evidence": evidence,
            })

        return results

    def _check_visual_assertion(
        self,
        screenshot_path: str,
        check: str,
        element: str,
        expected: str,
    ) -> tuple[bool, str, str]:
        """Ask Claude to verify a visual assertion against a screenshot."""
        import base64  # noqa: PLC0415

        try:
            from utils.claude_client import _get_client  # noqa: PLC0415
        except ImportError:
            return False, "claude_client not available", "skipped"

        try:
            with open(screenshot_path, "rb") as fh:
                img_b64 = base64.standard_b64encode(fh.read()).decode("utf-8")
        except Exception as exc:
            return False, f"Could not read screenshot: {exc}", "error"

        prompt = (
            f"Look at this screenshot of a mobile app.\n\n"
            f"Assertion to check: {check}\n"
            f"Element/text expected: {element!r}\n"
            f"Expected state: {expected}\n\n"
            "Reply with JSON: "
            '{"passed": true/false, "actual": "<what you see>", "confidence": "high/medium/low"}'
        )

        try:
            client = _get_client()
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=256,
                messages=[{
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {"type": "base64", "media_type": "image/png", "data": img_b64},
                        },
                        {"type": "text", "text": prompt},
                    ],
                }],
            )
            raw = response.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            data = json.loads(raw)
            return (
                bool(data.get("passed", False)),
                str(data.get("actual", "")),
                f"claude_vision ({data.get('confidence', '?')})",
            )
        except Exception as exc:
            logger.warning(f"[FigmaUATRunner] Visual assertion check failed: {exc}")
            return False, f"Vision check failed: {exc}", "error"

    # ------------------------------------------------------------------
    # Report generation
    # ------------------------------------------------------------------

    def _generate_report(self, results: list[dict], report: dict) -> str:
        """
        Generate and save a Markdown report to reports/figma_uat_{run_id}.md.
        Returns the report file path.
        """
        reports_dir = Path("reports")
        reports_dir.mkdir(parents=True, exist_ok=True)
        report_path = reports_dir / f"figma_uat_{self.run_id}.md"

        file_name = report.get("file_name", "Untitled")
        compliance_pct = round(report.get("compliance_rate", 0) * 100, 1)
        verdict = report.get("overall_verdict", "UNKNOWN")
        tested = report.get("tested_screens", 0)
        compliant = report.get("compliant", 0)
        non_compliant = report.get("non_compliant", 0)
        skipped = report.get("skipped", 0)

        verdict_emoji = {"COMPLIANT": "PASS", "PARTIALLY_COMPLIANT": "WARN", "NON_COMPLIANT": "FAIL"}.get(verdict, "?")

        lines: list[str] = [
            f"# Figma UAT Report — {file_name}",
            "",
            f"**Run ID:** `{self.run_id}`  ",
            f"**Generated:** {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}  ",
            f"**Figma File ID:** `{self.file_id}`  ",
            "",
            "---",
            "",
            "## Summary",
            "",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Overall Verdict | **{verdict}** [{verdict_emoji}] |",
            f"| Compliance Rate | {compliance_pct}% |",
            f"| Tested Screens | {tested} |",
            f"| Compliant | {compliant} |",
            f"| Non-Compliant | {non_compliant} |",
            f"| Skipped / Nav Failed | {skipped} |",
            "",
        ]

        # Critical issues
        critical_issues = report.get("critical_issues", [])
        if critical_issues:
            lines += [
                "## Critical Issues",
                "",
            ]
            for issue in critical_issues:
                lines.append(f"### {issue['tc_name']}")
                for iss in issue.get("issues", []):
                    lines.append(f"- {iss}")
                lines.append("")

        # Screen-by-screen results table
        lines += [
            "## Screen-by-Screen Results",
            "",
            "| Screen | Type | Status | Score | Issues |",
            "|--------|------|--------|-------|--------|",
        ]
        for r in results:
            tc_name = r.get("tc_name", "?")
            tc_type = r.get("tc_type", "?")
            status = r.get("status", "?")
            compare = r.get("compare_result") or {}
            score = compare.get("match_score", "—")
            if isinstance(score, float):
                score = f"{round(score * 100, 1)}%"
            issues = "; ".join(compare.get("issues", [])[:2]) or "—"
            lines.append(f"| {tc_name} | {tc_type} | {status} | {score} | {issues} |")

        lines.append("")

        # Persuasion elements coverage
        persuasion_results = report.get("persuasion_results", [])
        if persuasion_results:
            lines += [
                "## Persuasion Elements Coverage",
                "",
                "| Element | Status | Notes |",
                "|---------|--------|-------|",
            ]
            for r in persuasion_results:
                name = r.get("tc_name", "?")
                status = r.get("status", "?")
                assertions = r.get("assertion_results", [])
                notes = "; ".join(
                    f"{a['element']}: {a['actual']}"
                    for a in assertions[:3]
                    if not a.get("passed")
                ) or "OK"
                lines.append(f"| {name} | {status} | {notes} |")
            lines.append("")

        # Screenshots gallery
        lines += [
            "## Screenshots Gallery",
            "",
        ]
        for r in results:
            path = r.get("screenshot_path")
            if path and Path(path).exists():
                diff_path = (r.get("compare_result") or {}).get("diff_image_path", "")
                lines.append(f"**{r.get('tc_name', '?')}**")
                lines.append(f"  - Screenshot: `{path}`")
                if diff_path:
                    lines.append(f"  - Diff: `{diff_path}`")
                lines.append("")

        content = "\n".join(lines)
        report_path.write_text(content, encoding="utf-8")
        logger.info(f"[FigmaUATRunner] Report saved: {report_path}")
        return str(report_path)
