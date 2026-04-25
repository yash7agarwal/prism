"""LLM cascade behavior — pins current Anthropic→Gemini fallback semantics.

This test suite is the prerequisite for any future extraction of the shared
retry+fallback loop into utils/llm_cascade.py. Per the 2026-04-25 cross-project
audit (anti-pattern #1 in _workspace-os/memory/learnings.md L-W04), refactor
without test coverage inverts the dependency order. So we pin first, then
extract in a follow-up session.

The two functions under test (utils/claude_client.py):
  - ask(): synthesis text. Cascade behavior:
      * RateLimitError → exp-backoff retry; all retries exhausted → RuntimeError.
        NO Gemini fallback on rate-limit in ask().
      * Credit/billing (BadRequestError/APIStatusError with "credit balance" |
        "usage limits" | "billing" in message) → immediate Gemini fallback.
      * 5xx → exp-backoff retry; all retries exhausted → re-raise.
        NO Gemini fallback on 5xx in ask().
  - ask_with_tools(): tool-using messages. Cascade behavior is DIFFERENT:
      * RateLimitError → exp-backoff retry; if all fail → Gemini fallback.
      * Credit/billing → immediate Gemini fallback (same as ask()).
      * 5xx → immediate Gemini fallback (different from ask(): no retry).

If a future "shared cascade" extraction merges these two into one policy, this
suite must update to reflect the chosen merged behavior — and the change must
be intentional, not accidental.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Make the project root importable so `from utils import claude_client` resolves.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import anthropic  # noqa: E402

from utils import claude_client  # noqa: E402


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _make_response(text: str) -> MagicMock:
    """Build a fake anthropic.types.Message return value."""
    resp = MagicMock()
    resp.content = [MagicMock(text=text)]
    resp.usage = MagicMock(input_tokens=1, output_tokens=1)
    return resp


def _credit_error() -> anthropic.BadRequestError:
    """Build a BadRequestError that the cascade detects as a billing exhaustion."""
    response = MagicMock(status_code=400)
    return anthropic.BadRequestError(
        message="Your credit balance is too low to access the Anthropic API.",
        response=response,
        body=None,
    )


def _rate_limit_error() -> anthropic.RateLimitError:
    response = MagicMock(status_code=429)
    return anthropic.RateLimitError(
        message="rate_limit_exceeded",
        response=response,
        body=None,
    )


def _server_error() -> anthropic.APIStatusError:
    response = MagicMock(status_code=503)
    err = anthropic.APIStatusError(
        message="upstream service unavailable",
        response=response,
        body=None,
    )
    # Some anthropic versions read .status_code off the exception itself
    err.status_code = 503
    return err


@pytest.fixture(autouse=True)
def _reset_client_singleton():
    """claude_client caches the Anthropic client at module level. Reset between tests."""
    claude_client._client = None
    yield
    claude_client._client = None


@pytest.fixture(autouse=True)
def _no_real_sleeps(monkeypatch):
    """Don't actually sleep through exp-backoff in tests."""
    monkeypatch.setattr(claude_client.time, "sleep", lambda *_a, **_k: None)


@pytest.fixture(autouse=True)
def _claude_provider(monkeypatch):
    """Default: LLM_PROVIDER=claude. Tests can override to test the gemini-routing branch."""
    monkeypatch.setenv("LLM_PROVIDER", "claude")
    # Avoid the "ANTHROPIC_API_KEY not set" guard in _get_client when we don't patch _get_client.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")


# --------------------------------------------------------------------------
# ask() cascade
# --------------------------------------------------------------------------

class TestAskHappyPath:
    def test_returns_text_no_fallback(self):
        fake_anthropic = MagicMock()
        fake_anthropic.messages.create.return_value = _make_response("hello world")
        with patch.object(claude_client, "_get_client", return_value=fake_anthropic):
            result = claude_client.ask("ping", retries=3)
        assert result == "hello world"
        # Anthropic was called once — no fallback path triggered.
        assert fake_anthropic.messages.create.call_count == 1


class TestAskCreditFallback:
    def test_credit_error_falls_back_to_gemini_immediately(self):
        fake_anthropic = MagicMock()
        fake_anthropic.messages.create.side_effect = _credit_error()
        fake_gemini_module = MagicMock()
        fake_gemini_module.ask.return_value = "gemini said hi"

        with patch.object(claude_client, "_get_client", return_value=fake_anthropic), \
             patch.dict(sys.modules, {"utils.gemini_client": fake_gemini_module}):
            result = claude_client.ask("ping", retries=3)

        assert result == "gemini said hi"
        # Anthropic called once (no retry on billing).
        assert fake_anthropic.messages.create.call_count == 1
        # Gemini called exactly once.
        assert fake_gemini_module.ask.call_count == 1

    def test_usage_limits_message_also_falls_back(self):
        # "usage limits" wording is one of the three trigger strings.
        err_response = MagicMock(status_code=400)
        err = anthropic.BadRequestError(
            message="You have exceeded your usage limits for this billing period.",
            response=err_response,
            body=None,
        )
        fake_anthropic = MagicMock()
        fake_anthropic.messages.create.side_effect = err
        fake_gemini_module = MagicMock(ask=MagicMock(return_value="g"))

        with patch.object(claude_client, "_get_client", return_value=fake_anthropic), \
             patch.dict(sys.modules, {"utils.gemini_client": fake_gemini_module}):
            assert claude_client.ask("ping") == "g"


class TestAskRateLimit:
    def test_rate_limit_retries_and_then_raises_runtime_error(self):
        # ask() does NOT fall back to Gemini on rate-limit. It exhausts retries then raises.
        fake_anthropic = MagicMock()
        fake_anthropic.messages.create.side_effect = _rate_limit_error()

        with patch.object(claude_client, "_get_client", return_value=fake_anthropic):
            with pytest.raises(RuntimeError, match="Claude call failed after 3 retries"):
                claude_client.ask("ping", retries=3)

        # All 3 retries were attempted.
        assert fake_anthropic.messages.create.call_count == 3

    def test_rate_limit_then_success_returns(self):
        fake_anthropic = MagicMock()
        fake_anthropic.messages.create.side_effect = [
            _rate_limit_error(),
            _make_response("eventually ok"),
        ]
        with patch.object(claude_client, "_get_client", return_value=fake_anthropic):
            assert claude_client.ask("ping", retries=3) == "eventually ok"


class TestAskServerError:
    def test_5xx_retries_and_then_raises(self):
        # ask() does NOT fall back on 5xx. Retries with backoff, then re-raises.
        fake_anthropic = MagicMock()
        fake_anthropic.messages.create.side_effect = _server_error()

        with patch.object(claude_client, "_get_client", return_value=fake_anthropic):
            with pytest.raises(anthropic.APIStatusError):
                claude_client.ask("ping", retries=3)

        # 3 attempts; on the last one the `attempt < retries - 1` guard fails so it raises.
        assert fake_anthropic.messages.create.call_count == 3


# --------------------------------------------------------------------------
# ask_with_tools() cascade — DIFFERENT from ask()
# --------------------------------------------------------------------------

class TestAskWithToolsHappyPath:
    def test_returns_message_no_fallback(self):
        fake_anthropic = MagicMock()
        fake_msg = MagicMock(content=[])
        fake_anthropic.messages.create.return_value = fake_msg
        with patch.object(claude_client, "_get_client", return_value=fake_anthropic):
            result = claude_client.ask_with_tools(
                messages=[{"role": "user", "content": "hi"}],
                tools=[],
                retries=3,
            )
        assert result is fake_msg
        assert fake_anthropic.messages.create.call_count == 1


class TestAskWithToolsCreditFallback:
    def test_credit_error_falls_back_to_gemini(self):
        fake_anthropic = MagicMock()
        fake_anthropic.messages.create.side_effect = _credit_error()
        gemini_msg = MagicMock(name="gemini-response")
        fake_gemini_module = MagicMock()
        fake_gemini_module.ask_with_tools.return_value = gemini_msg

        with patch.object(claude_client, "_get_client", return_value=fake_anthropic), \
             patch.dict(sys.modules, {"utils.gemini_client": fake_gemini_module}):
            result = claude_client.ask_with_tools(
                messages=[{"role": "user", "content": "hi"}], tools=[], retries=3,
            )

        assert result is gemini_msg
        assert fake_gemini_module.ask_with_tools.call_count == 1


class TestAskWithToolsRateLimit:
    def test_rate_limit_retries_then_falls_back_to_gemini(self):
        # Behavior in ask_with_tools is: retry rate-limit, and if all retries also rate-limit,
        # fall back to Gemini (different from ask() which raises RuntimeError).
        fake_anthropic = MagicMock()
        fake_anthropic.messages.create.side_effect = _rate_limit_error()
        gemini_msg = MagicMock(name="gemini-response")
        fake_gemini_module = MagicMock()
        fake_gemini_module.ask_with_tools.return_value = gemini_msg

        with patch.object(claude_client, "_get_client", return_value=fake_anthropic), \
             patch.dict(sys.modules, {"utils.gemini_client": fake_gemini_module}):
            result = claude_client.ask_with_tools(
                messages=[{"role": "user", "content": "hi"}], tools=[], retries=3,
            )

        assert result is gemini_msg
        # First attempt + 2 retries before fallback = 3 anthropic calls.
        assert fake_anthropic.messages.create.call_count == 3
        assert fake_gemini_module.ask_with_tools.call_count == 1


class TestAskWithToolsServerError:
    def test_5xx_immediately_falls_back_to_gemini(self):
        # In ask_with_tools, 5xx falls back IMMEDIATELY (no retry) — different from ask().
        fake_anthropic = MagicMock()
        fake_anthropic.messages.create.side_effect = _server_error()
        gemini_msg = MagicMock(name="gemini-response")
        fake_gemini_module = MagicMock()
        fake_gemini_module.ask_with_tools.return_value = gemini_msg

        with patch.object(claude_client, "_get_client", return_value=fake_anthropic), \
             patch.dict(sys.modules, {"utils.gemini_client": fake_gemini_module}):
            result = claude_client.ask_with_tools(
                messages=[{"role": "user", "content": "hi"}], tools=[], retries=3,
            )

        assert result is gemini_msg
        # Anthropic called exactly once — NO retry on 5xx in ask_with_tools.
        assert fake_anthropic.messages.create.call_count == 1


class TestAskWithToolsBothFail:
    def test_credit_error_then_gemini_also_fails_raises_gemini_error(self):
        fake_anthropic = MagicMock()
        fake_anthropic.messages.create.side_effect = _credit_error()
        fake_gemini_module = MagicMock()
        fake_gemini_module.ask_with_tools.side_effect = RuntimeError("gemini also down")

        with patch.object(claude_client, "_get_client", return_value=fake_anthropic), \
             patch.dict(sys.modules, {"utils.gemini_client": fake_gemini_module}):
            with pytest.raises(RuntimeError, match="gemini also down"):
                claude_client.ask_with_tools(
                    messages=[{"role": "user", "content": "hi"}], tools=[], retries=3,
                )


# --------------------------------------------------------------------------
# Provider-switch via env var (LLM_PROVIDER=gemini)
# --------------------------------------------------------------------------

class TestProviderEnvVar:
    def test_provider_gemini_routes_ask_directly_to_gemini(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "gemini")
        fake_gemini_module = MagicMock()
        fake_gemini_module.ask.return_value = "via gemini"
        # If anything calls Anthropic, the test would fail — _get_client should not be invoked.
        with patch.object(claude_client, "_get_client", side_effect=AssertionError("anthropic invoked!")), \
             patch.dict(sys.modules, {"utils.gemini_client": fake_gemini_module}):
            result = claude_client.ask("ping", retries=3)
        assert result == "via gemini"

    def test_provider_gemini_routes_ask_with_tools_directly_to_gemini(self, monkeypatch):
        monkeypatch.setenv("LLM_PROVIDER", "gemini")
        fake_gemini_module = MagicMock()
        fake_gemini_module.ask_with_tools.return_value = MagicMock(name="g-msg")
        with patch.object(claude_client, "_get_client", side_effect=AssertionError("anthropic invoked!")), \
             patch.dict(sys.modules, {"utils.gemini_client": fake_gemini_module}):
            result = claude_client.ask_with_tools(
                messages=[{"role": "user", "content": "hi"}], tools=[],
            )
        assert result is fake_gemini_module.ask_with_tools.return_value
