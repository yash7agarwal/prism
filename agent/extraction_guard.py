"""Extraction guard — last-mile validation before knowledge-graph upserts.

Why this exists:
Live UAT on a fresh "Platinum industries limited" project produced 10
entities, all of `entity_type='trend'` — including the project itself
("Platinum Industries is a leading PVC stabilizer manufacturer"), random
people ("Dr. Michael Schiller"), regulators ("European Chemicals Agency"),
and platinum-the-metal commentary ("platinum market deficit of 240 koz").

Three layers were missing:
  1. **Type whitelist.** The DB column is free `String(50)`. Synthesis
     can emit any string and storage accepts it; a typo or category-as-
     type collapse silently corrupts downstream filtering.
  2. **Self-extraction guard.** Nothing prevents the agent from
     extracting the user's own project as a competitor or trend — which
     is what produced the "Platinum Industries is a leading..." trend.
  3. **Trivial-name reject.** "Industry", "Market", "Plastics" — too
     generic to be useful; quietly drop them.

This module is the single chokepoint. Every entity upsert from a synthesis
result MUST flow through `validate_extraction()`. Reject reasons get
logged so we can audit drift without the user having to spot it.

Design intent: this is intentionally cheap (string ops only), runs in-
process, and is fully unit-testable without any LLM or DB.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


# The de-facto type vocabulary observed across agents. Anything outside
# this set is rejected — keeps the entity_type column to a known set so
# UI filters (`entity_type='company'`) stay reliable.
ALLOWED_ENTITY_TYPES: frozenset[str] = frozenset({
    "company", "app", "trend", "regulation", "regulator",
    "person", "technology", "article", "market", "project",
})

# Names that almost always mean "the synthesizer hallucinated a generic"
# — they survive because the LLM picked the path of least resistance.
# Block them before they pollute the KG.
_TRIVIAL_NAMES: frozenset[str] = frozenset({
    "industry", "market", "trends", "trend", "growth", "regulation",
    "competitor", "competitors", "company", "the market", "the industry",
})


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    reason: str | None = None  # populated only when ok=False, for logging


def _normalize(s: str) -> str:
    """Lowercase + collapse whitespace + strip the most common suffixes
    so "Platinum Industries Ltd." matches "Platinum Industries"."""
    s = (s or "").lower().strip()
    s = re.sub(r"\s+", " ", s)
    # Strip common corporate suffixes — they're noise for matching.
    s = re.sub(
        r"\s+(ltd\.?|limited|inc\.?|llc|pvt\.?|private|corp\.?|corporation|co\.?|gmbh|sa|plc)$",
        "",
        s,
    )
    return s.strip(" .,")


def _is_self_reference(extracted_name: str, project_name: str) -> bool:
    """True when the extracted entity is the project itself.

    Uses a normalized substring containment check rather than exact match
    so "Platinum Industries is a leading PVC stabilizer manufacturer"
    (the synthesizer's verbose name for the project) still matches the
    short canonical "Platinum Industries Ltd.".
    """
    if not extracted_name or not project_name:
        return False
    e = _normalize(extracted_name)
    p = _normalize(project_name)
    if not e or not p:
        return False
    # Either side fully contained in the other = self-reference. The
    # symmetric check catches both "Platinum Industries" inside the long
    # form and the (rarer) project name being a longer phrase.
    return p in e or e in p


def validate_extraction(
    name: str,
    entity_type: str,
    project_name: str,
) -> ValidationResult:
    """Gate every knowledge-graph upsert.

    Returns ValidationResult(ok=True) when the extraction is allowed to
    persist. Otherwise ok=False with a `reason` string the caller should
    log so we can spot drift later.

    Rules (apply in order, first hit wins):
      1. Empty / too-short name → reject.
      2. Trivial generic name → reject.
      3. entity_type not in whitelist → reject.
      4. Self-reference to the project itself → reject.
    """
    n = (name or "").strip()
    if len(n) < 3:
        return ValidationResult(False, f"name too short: {name!r}")

    if _normalize(n) in _TRIVIAL_NAMES:
        return ValidationResult(False, f"trivial generic name: {name!r}")

    et = (entity_type or "").strip().lower()
    if et not in ALLOWED_ENTITY_TYPES:
        return ValidationResult(
            False,
            f"entity_type {entity_type!r} not in whitelist {sorted(ALLOWED_ENTITY_TYPES)}",
        )

    if _is_self_reference(n, project_name):
        return ValidationResult(
            False,
            f"self-reference: {name!r} matches project {project_name!r}",
        )

    return ValidationResult(True)


def coerce_entity_type(synthesizer_category: str | None) -> str:
    """Map a synthesis-output category string to a whitelisted entity_type.

    Synthesizer prompts emit different vocabularies depending on context
    ("category" for trends, "type" for findings, etc.). Centralize the
    mapping so industry_research_agent doesn't have to silently force
    everything to "trend" when the synthesizer returned "regulation" or
    "market_structure" or "company".
    """
    if not synthesizer_category:
        return "trend"
    c = synthesizer_category.strip().lower()
    direct: dict[str, str] = {
        "company": "company",
        "competitor": "company",
        "app": "app",
        "trend": "trend",
        "consumer_behavior": "trend",
        "demographics": "trend",
        "market_structure": "trend",
        "regulation": "regulation",
        "regulatory": "regulation",
        "regulator": "regulator",
        "person": "person",
        "executive": "person",
        "technology": "technology",
        "article": "article",
        "publication": "article",
        "market": "market",
        "general": "trend",
    }
    if c in direct:
        return direct[c]
    # Unknown category → log and default to trend so we don't block extraction
    # entirely; the validator will catch any second-order issues.
    logger.warning(f"[extraction_guard] unknown synthesizer category {c!r}, defaulting to trend")
    return "trend"
