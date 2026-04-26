"""Pin the credit/billing detection that gates Claude→Gemini fallback (v0.17.3).

Why this exists:
v0.17.0–.2's ask() / ask_with_tools() classified EVERY 400 from Anthropic
as a credit problem, which caused malformed prompts and tier-restricted
model errors to silently fall through to Gemini → 5+ minute waste before
final failure. v0.17.3 tightens the detection. These tests pin the
canonical strings so future "small refactor of error handling" can't
regress the gate.
"""
from __future__ import annotations

import pytest

from utils.claude_client import _is_credit_or_billing


@pytest.mark.parametrize("err_text", [
    "your credit balance is too low to process this request",
    "credit balance",
    "billing not configured for this api key",
    "monthly usage limit reached for your tier",
    "usage limits exceeded — upgrade your plan",
    "payment required for this model",
])
def test_credit_strings_trigger_fallback(err_text):
    assert _is_credit_or_billing(err_text), f"expected credit-detection on: {err_text!r}"


@pytest.mark.parametrize("err_text", [
    "prompt too long",
    "invalid model: claude-fake-1",
    "messages.0.content: required",
    "max_tokens must be positive",
    "rate limit exceeded — retry later",
    "internal server error",
    "",
])
def test_non_credit_400s_do_not_trigger_fallback(err_text):
    """Non-credit 400s must surface — falling to Gemini for these wastes 5+ min."""
    assert not _is_credit_or_billing(err_text), (
        f"non-credit error wrongly classified as billing: {err_text!r}"
    )
