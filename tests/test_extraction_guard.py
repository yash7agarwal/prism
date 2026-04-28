"""Tests for agent/extraction_guard.

Pin the cases that were producing the "Platinum Industries" UAT bugs:
  - the project itself shouldn't be extracted as a trend/competitor
  - regulators / people / vague generics shouldn't pass the type whitelist
  - synthesizer category strings should map to a coherent entity_type
"""
from __future__ import annotations

import pytest

from agent.extraction_guard import (
    ALLOWED_ENTITY_TYPES,
    coerce_entity_type,
    validate_extraction,
)


# ---- Self-extraction guard ----

@pytest.mark.parametrize("extracted", [
    "Platinum Industries",
    "Platinum Industries Ltd.",
    "platinum industries limited",
    "Platinum Industries is a leading PVC stabilizer manufacturer",  # the actual UAT failure
])
def test_self_reference_rejected(extracted):
    r = validate_extraction(extracted, "company", project_name="Platinum Industries Ltd.")
    assert not r.ok
    assert "self-reference" in (r.reason or "")


def test_legitimate_competitor_passes():
    r = validate_extraction("Avi Additives Pvt Ltd", "company", project_name="Platinum Industries Ltd.")
    assert r.ok, r.reason


# ---- Type whitelist ----

def test_unknown_type_rejected():
    r = validate_extraction("Some Trend", "novel_thing", project_name="MyCo")
    assert not r.ok
    assert "entity_type" in (r.reason or "")


@pytest.mark.parametrize("etype", sorted(ALLOWED_ENTITY_TYPES))
def test_whitelisted_type_accepted(etype):
    r = validate_extraction("A specific named pattern", etype, project_name="MyCo")
    assert r.ok, r.reason


# ---- Trivial-name reject ----

@pytest.mark.parametrize("name", ["Industry", "Market", "Trends", "Competitor"])
def test_trivial_names_rejected(name):
    r = validate_extraction(name, "trend", project_name="MyCo")
    assert not r.ok
    assert "trivial" in (r.reason or "")


def test_too_short_rejected():
    r = validate_extraction("A", "trend", project_name="MyCo")
    assert not r.ok
    assert "too short" in (r.reason or "")


# ---- v0.18.3: synthesizer placeholder rejection ----

@pytest.mark.parametrize("placeholder", [
    "Competitor 1",
    "Competitor 2 from the 4 findings",  # actual UAT failure on Platinum project
    "Competitor #3",
    "competitor 1",
    "Company A",
    "Company X",
    "Player B",
    "Player 5",
    "Example 1",
    "Sample 2",
    "Item 3",
    "TBD",
    "TODO",
    "XXX",
    "N/A",
])
def test_placeholder_names_rejected(placeholder):
    r = validate_extraction(placeholder, "company", project_name="MyCo")
    assert not r.ok, f"{placeholder!r} should be rejected as placeholder"
    assert "placeholder" in (r.reason or "").lower() or "templated" in (r.reason or "").lower(), (
        f"reason {r.reason!r} should mention placeholder/templated"
    )


@pytest.mark.parametrize("real_name", [
    "OpenAI",
    "Anthropic",
    "Google Gemini",
    "Mistral AI",
    "Cohere",
    "Krutrim",
    "Sarvam.ai",  # legitimate real names that contain digits/dots/etc.
    "Avi Additives Pvt Ltd",
    "PVC Industries 2026 Annual Report",  # has a year — must not get caught by digit rules
])
def test_real_names_not_rejected_as_placeholders(real_name):
    """Real company names — including ones with digits, dots, or year suffixes —
    must NOT trip the placeholder detector. The patterns target leading
    'Competitor N' / 'Company X' templates, not arbitrary digit appearance."""
    r = validate_extraction(real_name, "company", project_name="ZebraCorp")
    # If rejected, must be for a different reason (not placeholder)
    if not r.ok:
        assert "placeholder" not in (r.reason or "").lower(), (
            f"real name {real_name!r} wrongly flagged as placeholder: {r.reason}"
        )


# ---- Category coercion ----

@pytest.mark.parametrize("synth_category,expected_type", [
    ("regulation", "regulation"),
    ("regulatory", "regulation"),
    ("market_structure", "trend"),
    ("consumer_behavior", "trend"),
    ("company", "company"),
    ("competitor", "company"),
    ("person", "person"),
    ("technology", "technology"),
    (None, "trend"),
    ("", "trend"),
    ("blah_unknown_category", "trend"),  # safe default
])
def test_coerce_entity_type(synth_category, expected_type):
    assert coerce_entity_type(synth_category) == expected_type
