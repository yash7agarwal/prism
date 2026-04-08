"""
agent/variant_detector.py — A/B variant fingerprinting

Detects which A/B test variant an account is enrolled in by fingerprinting
the post-login home screen. Groups accounts by variant similarity.

How it works:
1. After login, capture screenshot + UI tree
2. Extract key signals (modules present, hero layout, CTAs)
3. Hash the signals to create a variant fingerprint
4. Compare fingerprints across accounts to group them
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from utils.claude_client import ask
from utils.config import get

if TYPE_CHECKING:
    from tools.android_device import AndroidDevice

logger = logging.getLogger(__name__)

# System prompt for Claude when analyzing post-login UI
_FINGERPRINT_SYSTEM = """You are a mobile UI analyst specialising in A/B testing.
You will be given an Android UI accessibility tree (XML) from a MakeMyTrip app home screen.
Extract the key structural signals that distinguish different A/B variants.
Return ONLY valid JSON — no prose, no markdown fences."""

_FINGERPRINT_PROMPT = """Analyse the following MakeMyTrip post-login UI tree and extract variant signals.

UI Tree (may be truncated):
{ui_tree}

Return a JSON object with exactly these keys:
{{
  "modules": ["<list of top-level module names / section headings visible>"],
  "hero_text": "<primary banner or hero section text, or empty string>",
  "cta_text": ["<list of distinct call-to-action button texts>"],
  "layout_type": "<one of: grid | carousel | list | tabbed | unknown>"
}}

Be concise. For modules and cta_text, include only the first 10 items max."""


class VariantDetector:
    """Fingerprints the post-login home screen to detect A/B test variants."""

    def __init__(self, run_id: str | None = None):
        self.run_id = run_id or f"vd_{int(time.time())}"
        evidence_root = Path(get("uat.evidence_dir", ".tmp/evidence"))
        self._screenshot_dir = evidence_root / self.run_id / "_variant_fingerprints"
        self._screenshot_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fingerprint_session(self, device: "AndroidDevice", account_id: str) -> dict:
        """
        Capture post-login screenshot + UI tree and return a variant fingerprint.

        Returns:
            {
                "account_id": str,
                "fingerprint_hash": str,
                "signals": {
                    "modules": [...],
                    "hero_text": str,
                    "cta_text": [...],
                    "layout_type": str
                },
                "screenshot_path": str
            }
        """
        logger.info(f"[VariantDetector] Fingerprinting account: {account_id}")

        # 1. Screenshot
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        screenshot_path = str(self._screenshot_dir / f"{account_id}_{timestamp}.png")
        device.screenshot(save_path=screenshot_path)

        # 2. UI tree
        ui_tree_xml = device.get_ui_tree()
        trimmed_xml = ui_tree_xml[:6000]  # keep context manageable for Claude

        # 3. Ask Claude to extract signals
        signals = self._extract_signals(trimmed_xml, account_id)

        # 4. Build a deterministic fingerprint hash from sorted signals
        fingerprint_hash = self._hash_signals(signals)

        logger.info(
            f"[VariantDetector] account={account_id} hash={fingerprint_hash} "
            f"layout={signals.get('layout_type')} modules={signals.get('modules')}"
        )

        return {
            "account_id": account_id,
            "fingerprint_hash": fingerprint_hash,
            "signals": signals,
            "screenshot_path": screenshot_path,
        }

    def group_by_variant(self, fingerprints: list[dict]) -> dict[str, list[str]]:
        """
        Group account IDs by their fingerprint hash.

        Args:
            fingerprints: list of dicts from fingerprint_session()

        Returns:
            {"variant_A": ["acc1", "acc3"], "variant_B": ["acc2"], ...}
            Variant labels are auto-assigned alphabetically by first-seen hash.
        """
        # Collect unique hashes in order of first appearance
        hash_order: list[str] = []
        hash_to_accounts: dict[str, list[str]] = {}

        for fp in fingerprints:
            h = fp["fingerprint_hash"]
            account_id = fp["account_id"]
            if h not in hash_to_accounts:
                hash_to_accounts[h] = []
                hash_order.append(h)
            hash_to_accounts[h].append(account_id)

        # Assign human-readable variant labels (variant_A, variant_B, ...)
        variant_labels = [f"variant_{chr(65 + i)}" for i in range(len(hash_order))]
        groups: dict[str, list[str]] = {}
        for label, h in zip(variant_labels, hash_order):
            groups[label] = hash_to_accounts[h]

        logger.info(f"[VariantDetector] Variant groups: { {k: v for k, v in groups.items()} }")
        return groups

    def classify_finding(self, finding: dict, variant_groups: dict) -> str:
        """
        Classify whether a finding represents a regression or a variant difference.

        Args:
            finding: must contain "account_id" and optionally "failing_accounts" (list).
                     If "failing_accounts" is present, uses that list for analysis.
                     Otherwise treats the single account_id as a failing account.
            variant_groups: output of group_by_variant()

        Returns:
            "REGRESSION"         — multiple accounts in the SAME variant all fail
            "VARIANT_DIFFERENCE" — failing accounts span DIFFERENT variant groups
            "INCONCLUSIVE"       — only one account, or no variant data available
        """
        # Determine the set of failing accounts
        failing: list[str] = finding.get("failing_accounts") or [finding["account_id"]]

        if len(failing) < 2:
            return "INCONCLUSIVE"

        # Build reverse map: account_id -> variant_label
        account_to_variant: dict[str, str] = {}
        for variant_label, account_ids in variant_groups.items():
            for aid in account_ids:
                account_to_variant[aid] = variant_label

        # Collect the variants of all failing accounts
        failing_variants = {account_to_variant.get(aid) for aid in failing}
        failing_variants.discard(None)  # accounts not in variant map

        if not failing_variants:
            return "INCONCLUSIVE"

        if len(failing_variants) == 1:
            # All failures in the same variant -> regression
            return "REGRESSION"
        else:
            # Failures spread across multiple variants -> variant difference
            return "VARIANT_DIFFERENCE"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _extract_signals(self, ui_tree_xml: str, account_id: str) -> dict:
        """Ask Claude to extract UI signals from the XML tree."""
        prompt = _FINGERPRINT_PROMPT.format(ui_tree=ui_tree_xml)
        try:
            raw = ask(
                prompt,
                system=_FINGERPRINT_SYSTEM,
                max_tokens=512,
            )
            signals = json.loads(raw)
            # Ensure all expected keys are present with safe defaults
            signals.setdefault("modules", [])
            signals.setdefault("hero_text", "")
            signals.setdefault("cta_text", [])
            signals.setdefault("layout_type", "unknown")
            return signals
        except (json.JSONDecodeError, Exception) as e:
            logger.warning(
                f"[VariantDetector] Signal extraction failed for {account_id}: {e}. "
                "Using empty signals."
            )
            return {"modules": [], "hero_text": "", "cta_text": [], "layout_type": "unknown"}

    @staticmethod
    def _hash_signals(signals: dict) -> str:
        """
        Create a stable fingerprint hash from UI signals.
        Hash is based on sorted module names + hero text so minor ordering
        changes in the XML do not create false variant splits.
        """
        modules = sorted(str(m).lower().strip() for m in signals.get("modules", []))
        hero = str(signals.get("hero_text", "")).lower().strip()
        layout = str(signals.get("layout_type", "unknown")).lower().strip()
        key_tuple = tuple(modules) + (hero, layout)
        raw = json.dumps(key_tuple, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]
