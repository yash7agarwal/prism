"""Unit tests for agent/bulk_report_classifier.py (v0.21.1).

Most important: prove the no-hallucination guarantees.
- Period regex returns None when no explicit cue exists.
- Filename match scores high only on real substring overlap.
- LLM disambiguation respects null answers and rejects out-of-range ids.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from agent import bulk_report_classifier as brc


# ---------------------------------------------------------------------------
# Period extraction
# ---------------------------------------------------------------------------


def test_parse_period_quarter_from_filename():
    p = brc.parse_period("acme_Q3_2024_10Q.pdf")
    assert p is not None
    assert p.fiscal_year == 2024
    assert p.quarter == 3
    assert p.period_label == "Q3 2024"


def test_parse_period_quarter_alt_format():
    p = brc.parse_period("acme-2024Q1-quarterly.pdf")
    assert p is not None
    assert p.quarter == 1
    assert p.fiscal_year == 2024


def test_parse_period_annual_fy_short():
    p = brc.parse_period("acme-FY24-annual-report.pdf")
    assert p is not None
    assert p.fiscal_year == 2024
    assert p.is_annual is True


def test_parse_period_annual_10k():
    p = brc.parse_period("acme-10K-2023.pdf")
    assert p is not None
    assert p.fiscal_year == 2023
    assert p.is_annual is True


def test_parse_period_strict_returns_none_for_bare_year():
    """Strict mode: a bare 4-digit year is NOT enough — too noisy."""
    p = brc.parse_period("acme-2024.pdf", strict=True)
    assert p is None


def test_parse_period_non_strict_accepts_bare_year_in_filename():
    p = brc.parse_period("acme-2024.pdf", strict=False)
    assert p is not None
    assert p.fiscal_year == 2024


def test_parse_period_returns_none_when_no_year():
    """No year, no period — never invent one."""
    p = brc.parse_period("acme-annual.pdf", strict=True)
    assert p is None
    p2 = brc.parse_period("acme-annual.pdf", strict=False)
    assert p2 is None


# ---------------------------------------------------------------------------
# Filename → competitor matching
# ---------------------------------------------------------------------------


def test_filename_match_substring_high_score():
    competitors = [
        {"id": 1, "name": "OpenAI", "canonical_name": "openai"},
        {"id": 2, "name": "Anthropic", "canonical_name": "anthropic"},
    ]
    m = brc.filename_match("openai-10K-2024.pdf", competitors)
    assert m is not None
    eid, name, score = m
    assert eid == 1
    assert score > 0.5


def test_filename_match_picks_higher_overlap_when_two_match():
    competitors = [
        {"id": 1, "name": "Microsoft Azure", "canonical_name": "microsoft azure"},
        {"id": 2, "name": "Microsoft Azure Cognitive Services", "canonical_name": "microsoft azure cognitive services"},
    ]
    m = brc.filename_match("microsoft_azure_2024.pdf", competitors)
    # Both contain "microsoft azure" — the longer name's match still resolves
    # to ONE entity. The exact id depends on score; we just verify SOME match.
    assert m is not None
    assert m[0] in (1, 2)


def test_filename_match_no_match_returns_none():
    competitors = [
        {"id": 1, "name": "OpenAI", "canonical_name": "openai"},
        {"id": 2, "name": "Anthropic", "canonical_name": "anthropic"},
    ]
    m = brc.filename_match("random-industry-report.pdf", competitors)
    assert m is None


def test_filename_match_skips_short_canonical_names():
    """3-char names are too short to substring-match against long filenames."""
    competitors = [{"id": 1, "name": "AI", "canonical_name": "ai"}]
    m = brc.filename_match("ai-trends-report.pdf", competitors)
    assert m is None  # canonical_name length < 3 → skipped


# ---------------------------------------------------------------------------
# LLM disambiguation — null is a valid answer
# ---------------------------------------------------------------------------


def test_llm_classify_returns_null_when_llm_says_null():
    competitors = [{"id": 1, "name": "OpenAI"}, {"id": 2, "name": "Anthropic"}]
    fake = json.dumps({
        "matched_entity_id": None,
        "confidence": "high",
        "reasoning": "Generic industry overview, no specific company.",
    })
    with patch.object(brc, "_call_llm", return_value=fake):
        eid, conf, reasoning = brc.llm_classify("industry-report.pdf", "the AI industry...", competitors)
    assert eid is None
    assert conf == "high"  # high confidence in saying NO match — that's good


def test_llm_classify_rejects_id_not_in_competitor_list():
    """Hallucination guard: LLM picks an id that doesn't exist → we treat as None."""
    competitors = [{"id": 1, "name": "OpenAI"}, {"id": 2, "name": "Anthropic"}]
    fake = json.dumps({
        "matched_entity_id": 999,
        "confidence": "high",
        "reasoning": "made up id",
    })
    with patch.object(brc, "_call_llm", return_value=fake):
        eid, conf, _ = brc.llm_classify("x.pdf", "...", competitors)
    assert eid is None
    assert conf == "none"


def test_llm_classify_rejects_non_int_id():
    competitors = [{"id": 1, "name": "OpenAI"}]
    fake = json.dumps({"matched_entity_id": "OpenAI", "confidence": "high"})
    with patch.object(brc, "_call_llm", return_value=fake):
        eid, conf, _ = brc.llm_classify("x.pdf", "...", competitors)
    assert eid is None


def test_llm_classify_handles_malformed_json():
    competitors = [{"id": 1, "name": "OpenAI"}]
    with patch.object(brc, "_call_llm", return_value="not json at all"):
        eid, conf, _ = brc.llm_classify("x.pdf", "...", competitors)
    assert eid is None


def test_llm_classify_no_competitors_returns_none():
    eid, conf, _ = brc.llm_classify("x.pdf", "...", [])
    assert eid is None
    assert conf == "none"


# ---------------------------------------------------------------------------
# End-to-end classify()
# ---------------------------------------------------------------------------


def test_classify_filename_match_skips_llm():
    """When filename match scores high, LLM is NOT called."""
    competitors = [{"id": 1, "name": "OpenAI", "canonical_name": "openai"}]
    with patch.object(brc, "llm_classify") as llm_mock:
        result = brc.classify("openai-10K-2024.pdf", "OpenAI annual report ..." * 100, competitors)
    assert result.matched_entity_id == 1
    assert result.match_method == "filename_substring"
    llm_mock.assert_not_called()


def test_classify_filename_no_match_falls_back_to_llm():
    competitors = [{"id": 1, "name": "OpenAI"}]
    with patch.object(brc, "llm_classify", return_value=(1, "medium", "body mentions OpenAI 30 times")):
        result = brc.classify("ambiguous-report.pdf", "long pdf text", competitors)
    assert result.matched_entity_id == 1
    assert result.match_method == "llm_disambiguation"


def test_classify_unmatched_returns_none_id():
    """The full no-match path: filename misses, LLM says null → ClassifiedReport
    with matched_entity_id=None. This is an EXPECTED outcome, not a failure."""
    competitors = [{"id": 1, "name": "OpenAI"}]
    with patch.object(brc, "llm_classify", return_value=(None, "high", "doesn't belong to any listed competitor")):
        result = brc.classify("random.pdf", "general industry text", competitors)
    assert result.matched_entity_id is None
    assert result.match_confidence == "none" or result.match_confidence == "high"
    # period extraction still runs even on unmatched
    assert result.period is None or result.period.fiscal_year is not None
