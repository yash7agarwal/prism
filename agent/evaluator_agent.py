"""
agent/evaluator_agent.py — Scenario evaluation subagent

Takes an evidence pack from ScenarioRunnerAgent and evaluates it
against the expected outcome. Classifies: PASS / FAIL / PARTIAL / VARIANT_DIFFERENCE.

Variant-aware: uses variant groups to avoid misclassifying A/B differences as regressions.
"""
from __future__ import annotations

import json
import logging

from utils.claude_client import ask

logger = logging.getLogger(__name__)

_EVAL_SYSTEM = (
    "You are a senior QA engineer evaluating mobile app test results. "
    "Respond only in valid JSON. No markdown, no explanation."
)

_EVAL_PROMPT = """\
You are evaluating a QA test scenario against its expected outcome.

Scenario name: {scenario_name}
Scenario category: {category}
Expected outcome: {expected_outcome}
Severity: {severity}

Actual results per account:
{results_summary}

Evaluate whether the actual results match the expected outcome.

Respond with JSON:
{{
  "verdict": "<one of: PASS | FAIL | PARTIAL | VARIANT_DIFFERENCE | BLOCKED>",
  "summary": "<1-2 sentence explanation of the verdict>",
  "defects": [
    {{
      "account_id": "<account that failed>",
      "description": "<clear description of what went wrong>",
      "severity": "<critical | high | medium | low>",
      "steps_to_reproduce": "<brief reproduction steps>"
    }}
  ]
}}

Classification rules:
- PASS: all accounts achieved the expected outcome
- FAIL: one or more accounts failed to achieve the expected outcome (regression)
- PARTIAL: some accounts passed, some failed — and the failures do NOT align with A/B variant groups
- VARIANT_DIFFERENCE: failures align with known A/B variant groups (expected divergence, not a regression)
- BLOCKED: the scenario could not run at all (e.g. app crashed, login failed)
- Only include defect entries for accounts that FAILED
"""


class EvaluatorAgent:
    """
    Evaluates scenario results against their expected outcomes.

    Variant-aware: if failing accounts all belong to the same A/B variant group,
    the result is classified as VARIANT_DIFFERENCE rather than FAIL.
    """

    def __init__(self, variant_groups: dict[str, list[str]] | None = None):
        """
        Args:
            variant_groups: Output of VariantDetector.group_by_variant().
                            e.g. {"variant_A": ["acc1", "acc3"], "variant_B": ["acc2"]}
        """
        self.variant_groups = variant_groups or {}

        # Build reverse map: account_id -> variant_label
        self._account_to_variant: dict[str, str] = {}
        for label, accounts in self.variant_groups.items():
            for acc in accounts:
                self._account_to_variant[acc] = label

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate_scenario(self, scenario: dict, results: list[dict]) -> dict:
        """
        Evaluate all results for a single scenario.

        Args:
            scenario: {"name": str, "expected_outcome": str, "severity": str, "category": str}
            results:  List of ScenarioRunnerAgent result dicts for this scenario.

        Returns:
            Structured evaluation dict with verdict, counts, defects, etc.
        """
        scenario_name = scenario.get("name", "Unknown scenario")
        expected_outcome = scenario.get("expected_outcome", "")
        severity = scenario.get("severity", "medium")
        category = scenario.get("category", "happy_path")

        if not results:
            return self._blocked_result(scenario_name, "No results available for this scenario.")

        # Count passes and failures based on status
        passed_results = [r for r in results if r.get("status") == "completed"]
        failed_results = [r for r in results if r.get("status") != "completed"]

        pass_count = len(passed_results)
        fail_count = len(failed_results)

        # Collect evidence paths from all results
        evidence_paths = self._collect_evidence_paths(results)

        # Build a readable results summary for Claude
        results_summary = self._format_results_summary(results)

        # Get Claude's structured evaluation
        claude_eval = self._evaluate_with_claude(
            scenario={
                "name": scenario_name,
                "category": category,
                "expected_outcome": expected_outcome,
                "severity": severity,
            },
            results_summary=results_summary,
        )

        # Override verdict with VARIANT_DIFFERENCE if failing accounts align with a variant group
        verdict = claude_eval.get("verdict", "FAIL")
        if verdict == "FAIL" and fail_count > 0:
            override = self._check_variant_alignment(failed_results)
            if override:
                verdict = "VARIANT_DIFFERENCE"

        # Build variant analysis string
        variant_analysis = self._build_variant_analysis(passed_results, failed_results)

        return {
            "scenario_name": scenario_name,
            "verdict": verdict,
            "pass_count": pass_count,
            "fail_count": fail_count,
            "variant_analysis": variant_analysis,
            "defects": claude_eval.get("defects", []),
            "evidence_paths": evidence_paths,
            "summary": claude_eval.get("summary", ""),
        }

    def evaluate_run(self, scenarios: list[dict], all_results: list[dict]) -> dict:
        """
        Evaluate all scenarios in a run.

        Args:
            scenarios:   List of scenario dicts (from orchestrator/scenario generation).
            all_results: All ScenarioRunnerAgent results across all accounts.

        Returns:
            Aggregate evaluation dict with per-scenario breakdowns and overall verdict.
        """
        # Group results by scenario name
        results_by_scenario: dict[str, list[dict]] = {}
        for r in all_results:
            sname = r.get("scenario_name", "")
            results_by_scenario.setdefault(sname, [])
            results_by_scenario[sname].append(r)

        scenario_evaluations: list[dict] = []
        for scenario in scenarios:
            sname = scenario.get("name", "")
            scenario_results = results_by_scenario.get(sname, [])
            eval_result = self.evaluate_scenario(scenario, scenario_results)
            scenario_evaluations.append(eval_result)

        # Aggregate counts
        total = len(scenario_evaluations)
        passed = sum(1 for e in scenario_evaluations if e["verdict"] == "PASS")
        failed = sum(1 for e in scenario_evaluations if e["verdict"] == "FAIL")
        partial = sum(1 for e in scenario_evaluations if e["verdict"] == "PARTIAL")
        variant_diffs = sum(1 for e in scenario_evaluations if e["verdict"] == "VARIANT_DIFFERENCE")
        blocked = sum(1 for e in scenario_evaluations if e["verdict"] == "BLOCKED")

        # Count critical failures: FAIL verdict on critical-severity scenarios
        severity_map = {s.get("name", ""): s.get("severity", "medium") for s in scenarios}
        critical_failures = sum(
            1 for e in scenario_evaluations
            if e["verdict"] == "FAIL" and severity_map.get(e["scenario_name"]) == "critical"
        )

        # Overall verdict
        if failed == 0 and blocked == 0 and partial == 0:
            overall_verdict = "PASS"
        elif critical_failures > 0 or failed > total * 0.3:
            overall_verdict = "FAIL"
        else:
            overall_verdict = "PASS_WITH_ISSUES"

        return {
            "total_scenarios": total,
            "passed": passed,
            "failed": failed,
            "partial": partial,
            "variant_differences": variant_diffs,
            "blocked": blocked,
            "critical_failures": critical_failures,
            "scenario_evaluations": scenario_evaluations,
            "overall_verdict": overall_verdict,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _evaluate_with_claude(self, scenario: dict, results_summary: str) -> dict:
        """
        Ask Claude to analyse whether actual outcomes match the expected outcome.
        Returns a dict with verdict, summary, and defects.
        """
        prompt = _EVAL_PROMPT.format(
            scenario_name=scenario["name"],
            category=scenario.get("category", "happy_path"),
            expected_outcome=scenario.get("expected_outcome", "Not specified"),
            severity=scenario.get("severity", "medium"),
            results_summary=results_summary,
        )

        try:
            raw = ask(prompt, system=_EVAL_SYSTEM, max_tokens=1024)
            result = json.loads(raw)

            # Validate verdict
            allowed_verdicts = {"PASS", "FAIL", "PARTIAL", "VARIANT_DIFFERENCE", "BLOCKED"}
            if result.get("verdict") not in allowed_verdicts:
                result["verdict"] = "FAIL"

            result.setdefault("summary", "")
            result.setdefault("defects", [])
            return result

        except (json.JSONDecodeError, Exception) as e:
            logger.warning(
                f"[EvaluatorAgent] Claude evaluation failed for '{scenario['name']}': {e}"
            )
            return {
                "verdict": "BLOCKED",
                "summary": f"Evaluation could not be completed: {e}",
                "defects": [],
            }

    def _check_variant_alignment(self, failed_results: list[dict]) -> bool:
        """
        Return True if all failing accounts belong to the same A/B variant group.
        This indicates a VARIANT_DIFFERENCE rather than a regression.
        """
        if not self.variant_groups or not failed_results:
            return False

        failing_account_ids = [r.get("account_id", "") for r in failed_results]
        if not failing_account_ids:
            return False

        failing_variants = {self._account_to_variant.get(aid) for aid in failing_account_ids}
        failing_variants.discard(None)

        # All failures in exactly one variant group -> likely A/B difference
        return len(failing_variants) == 1

    def _build_variant_analysis(
        self,
        passed_results: list[dict],
        failed_results: list[dict],
    ) -> str:
        """Build a human-readable variant analysis string."""
        if not self.variant_groups:
            return "No variant group data available."

        lines: list[str] = []

        # Group passing accounts by variant
        for label, accounts in self.variant_groups.items():
            passed_in_variant = [
                r.get("account_id", "") for r in passed_results
                if r.get("account_id") in accounts
            ]
            failed_in_variant = [
                r.get("account_id", "") for r in failed_results
                if r.get("account_id") in accounts
            ]

            if passed_in_variant or failed_in_variant:
                line = f"{label}: "
                parts = []
                if passed_in_variant:
                    parts.append(f"{', '.join(passed_in_variant)} passed")
                if failed_in_variant:
                    parts.append(f"{', '.join(failed_in_variant)} failed")
                line += "; ".join(parts)
                lines.append(line)

        return " | ".join(lines) if lines else "No accounts in variant groups."

    def _collect_evidence_paths(self, results: list[dict]) -> list[str]:
        """Extract evidence file paths from a list of results."""
        paths: list[str] = []
        for r in results:
            evidence = r.get("evidence_pack", {})
            if isinstance(evidence, dict):
                session_dir = evidence.get("session_dir", "")
                if session_dir:
                    paths.append(session_dir)
        return paths

    def _format_results_summary(self, results: list[dict]) -> str:
        """Build a compact text summary of results for Claude's prompt."""
        lines: list[str] = []
        for r in results:
            account_id = r.get("account_id", "unknown")
            status = r.get("status", "unknown")
            outcome = r.get("final_outcome", "No outcome recorded")
            # Truncate long outcomes
            if len(outcome) > 300:
                outcome = outcome[:300] + "..."
            lines.append(f"- Account {account_id}: status={status} | outcome={outcome}")
        return "\n".join(lines) if lines else "No results."

    @staticmethod
    def _blocked_result(scenario_name: str, reason: str) -> dict:
        return {
            "scenario_name": scenario_name,
            "verdict": "BLOCKED",
            "pass_count": 0,
            "fail_count": 0,
            "variant_analysis": "N/A",
            "defects": [],
            "evidence_paths": [],
            "summary": reason,
        }
