from __future__ import annotations

import os
import time

import anthropic
from dotenv import load_dotenv

load_dotenv()

DEFAULT_MODEL = "claude-sonnet-4-6"
FAST_MODEL = "claude-haiku-4-5-20251001"

_client: anthropic.Anthropic | None = None


def _record(resp: Any, model: str, call_type: str) -> None:
    """Persist a cost_ledger row. Fail-silent."""
    try:
        from utils import cost_tracker
        usage = getattr(resp, "usage", None)
        cost_tracker.record(
            "claude",
            tokens_in=getattr(usage, "input_tokens", 0) or 0,
            tokens_out=getattr(usage, "output_tokens", 0) or 0,
            call_type=call_type,
            model=model,
        )
    except Exception:
        pass


def _provider() -> str:
    """Read LLM_PROVIDER env var. Defaults to 'claude'. Set to 'gemini' to use Google Gemini."""
    return os.environ.get("LLM_PROVIDER", "claude").lower()


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError(
                "ANTHROPIC_API_KEY not set. Add it to your .env file."
            )
        _client = anthropic.Anthropic(api_key=api_key)
    return _client


def ask(
    prompt: str,
    max_tokens: int = 1024,
    model: str = DEFAULT_MODEL,
    system: str = "",
    retries: int = 3,
) -> str:
    """Call the configured LLM provider and return the text response. Retries on transient errors."""
    if _provider() == "gemini":
        from utils import gemini_client
        return gemini_client.ask(
            prompt=prompt, max_tokens=max_tokens, model=model, system=system, retries=retries
        )

    messages = [{"role": "user", "content": prompt}]
    kwargs: dict = {"model": model, "max_tokens": max_tokens, "messages": messages}
    if system:
        kwargs["system"] = system

    import logging
    _logger = logging.getLogger(__name__)
    for attempt in range(retries):
        try:
            resp = _get_client().messages.create(**kwargs)
            _record(resp, model, "synthesis")
            return resp.content[0].text
        except anthropic.RateLimitError:
            time.sleep(2 ** attempt * 5)
        except (anthropic.BadRequestError, anthropic.APIStatusError) as e:
            # v0.17.3: log the actual error body so non-credit 400s surface
            # loudly. Previously every 400 fell to Gemini → 5-min Gemini-retry
            # waste because synthesis prompts triggering "prompt too long" or
            # "model not allowed on tier" got misclassified as billing.
            err_str = str(e).lower()
            _logger.warning(
                f"[claude] {type(e).__name__} (model={model}): {str(e)[:400]}"
            )
            if _is_credit_or_billing(err_str):
                _logger.warning("[claude] credit/billing limit — falling back to Gemini")
                from utils import gemini_client
                return gemini_client.ask(prompt=prompt, max_tokens=max_tokens, model=model, system=system, retries=retries)
            if hasattr(e, 'status_code') and e.status_code >= 500 and attempt < retries - 1:
                time.sleep(2 ** attempt * 2)
            else:
                # Non-credit 400 (bad model name, prompt too long, invalid
                # params): re-raise loudly. The Gemini cascade can't fix any
                # of these — falling through wastes minutes for nothing.
                raise
    raise RuntimeError(f"Claude call failed after {retries} retries")


# v0.17.3: pulled out so both ask() and ask_with_tools() agree on the
# definition. Strings checked are the canonical Anthropic API error
# fragments — anything else is NOT a credit problem.
_CREDIT_BILLING_FRAGMENTS: tuple[str, ...] = (
    "credit balance",
    "credit balance is too low",
    "usage limits",
    "monthly usage limit",
    "billing",
    "payment required",
)


def _is_credit_or_billing(err_str_lower: str) -> bool:
    """True iff the error message literally signals credit/billing exhaustion.

    Wider matches than this caused v0.17.0–.2 reports to mis-cascade to
    Gemini on every malformed-prompt 400, burning ~5 min per call before
    final failure. Keep this tight.
    """
    return any(frag in err_str_lower for frag in _CREDIT_BILLING_FRAGMENTS)


def ask_fast(prompt: str, max_tokens: int = 512) -> str:
    """Use the fast/cheap model for low-stakes tasks. Routes via ask() so provider switch applies."""
    return ask(prompt, max_tokens=max_tokens, model=FAST_MODEL)


def ask_vision(
    prompt: str,
    image_bytes: bytes,
    media_type: str = "image/png",
    max_tokens: int = 512,
    model: str = FAST_MODEL,
    system: str = "",
    retries: int = 3,
) -> str:
    """Send an image + text prompt to the configured vision provider. Returns text response.

    Uses FAST_MODEL by default for speed in navigation loops.
    Pass model=DEFAULT_MODEL for higher-accuracy verification calls.
    Routes to Gemini if LLM_PROVIDER=gemini in .env.
    """
    if _provider() == "gemini":
        from utils import gemini_client
        return gemini_client.ask_vision(
            prompt=prompt,
            image_bytes=image_bytes,
            media_type=media_type,
            max_tokens=max_tokens,
            model=model,
            system=system,
            retries=retries,
        )

    import base64

    img_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": img_b64},
                },
                {"type": "text", "text": prompt},
            ],
        }
    ]
    kwargs: dict = {"model": model, "max_tokens": max_tokens, "messages": messages}
    if system:
        kwargs["system"] = system

    for attempt in range(retries):
        try:
            resp = _get_client().messages.create(**kwargs)
            _record(resp, model, "vision")
            return resp.content[0].text
        except anthropic.RateLimitError:
            time.sleep(2 ** attempt * 5)
        except anthropic.APIStatusError as e:
            if e.status_code >= 500 and attempt < retries - 1:
                time.sleep(2 ** attempt * 2)
            else:
                raise
    raise RuntimeError(f"Claude vision call failed after {retries} retries")


def ask_with_tools(
    messages: list[dict],
    tools: list[dict],
    system: str = "",
    model: str = DEFAULT_MODEL,
    max_tokens: int = 4096,
    retries: int = 3,
) -> anthropic.types.Message:
    """
    Call Claude with tool definitions. Returns the full Message object.
    Used by subagents that need to inspect tool_use blocks in the response.

    Auto-falls back to Gemini when Claude hits billing limits (400 with
    'usage limits' message) or is unavailable. The Gemini response is
    wrapped in a compatible object so callers don't need to change.
    """
    if _provider() == "gemini":
        from utils import gemini_client
        return gemini_client.ask_with_tools(
            messages=messages, tools=tools, system=system,
            model=model, max_tokens=max_tokens, retries=retries,
        )

    kwargs: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "tools": tools,
        "messages": messages,
    }
    if system:
        kwargs["system"] = system

    import logging
    _logger = logging.getLogger(__name__)
    try:
        return _get_client().messages.create(**kwargs)
    except (anthropic.BadRequestError, anthropic.AuthenticationError) as e:
        # v0.17.3: same tightening as ask() — only fall to Gemini on real
        # credit/billing exhaustion, log all other 400s loudly.
        err_str = str(e).lower()
        _logger.warning(
            f"[claude] tool-use {type(e).__name__} (model={model}): {str(e)[:400]}"
        )
        if _is_credit_or_billing(err_str):
            _logger.warning("[claude] credit/billing limit — switching to Gemini")
            try:
                from utils import gemini_client
                return gemini_client.ask_with_tools(
                    messages=messages, tools=tools, system=system,
                    model=model, max_tokens=max_tokens, retries=retries,
                )
            except Exception as gemini_err:
                # Gemini's own ask_with_tools already attempts a Groq fallback
                # internally before raising, so this branch only fires when
                # both Gemini AND Groq exhausted. Re-raise.
                _logger.error(f"[claude] Gemini+Groq fallback ALSO failed: {gemini_err}", exc_info=True)
                raise gemini_err
        raise
    except anthropic.RateLimitError:
        # Rate limit (not billing) — retry with backoff then fall back
        for attempt in range(retries - 1):
            time.sleep(2 ** attempt * 5)
            try:
                return _get_client().messages.create(**kwargs)
            except anthropic.RateLimitError:
                continue
            except (anthropic.BadRequestError, anthropic.AuthenticationError) as e2:
                err_str2 = str(e2).lower()
                if "credit balance" in err_str2 or "usage limits" in err_str2:
                    from utils import gemini_client
                    return gemini_client.ask_with_tools(
                        messages=messages, tools=tools, system=system,
                        model=model, max_tokens=max_tokens, retries=retries,
                    )
                raise
        # All retries exhausted — fall back to Gemini
        import logging
        logging.getLogger(__name__).warning("[claude] Rate limit exhausted — falling back to Gemini")
        from utils import gemini_client
        return gemini_client.ask_with_tools(
            messages=messages, tools=tools, system=system,
            model=model, max_tokens=max_tokens, retries=retries,
        )
    except anthropic.APIStatusError as e:
        if e.status_code >= 500:
            from utils import gemini_client
            return gemini_client.ask_with_tools(
                messages=messages, tools=tools, system=system,
                model=model, max_tokens=max_tokens, retries=retries,
            )
        raise
