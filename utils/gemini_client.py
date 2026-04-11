"""Google Gemini client with API surface matching utils.claude_client.

Drop-in alternative when LLM_PROVIDER=gemini is set in .env. Uses Gemini 2.0 Flash
for both default and fast modes — it's fast enough and the free tier covers our volume.

No SDK dependency: uses raw HTTP via httpx (already a project dep).
"""
from __future__ import annotations

import base64
import logging
import os
import time

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# gemini-flash-latest is an alias that points to whatever's currently free.
# We tried gemini-2.5-flash-lite first but its free tier is only 20 RPD which
# we burn through quickly on a single project. flash-latest has higher headroom.
DEFAULT_MODEL = "gemini-flash-latest"
FAST_MODEL = "gemini-flash-latest"

_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


def _api_key() -> str:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise RuntimeError(
            "GEMINI_API_KEY not set in .env. Get one free at https://aistudio.google.com/apikey"
        )
    return key


def ask(
    prompt: str,
    max_tokens: int = 1024,
    model: str = DEFAULT_MODEL,
    system: str = "",
    retries: int = 3,
) -> str:
    """Call Gemini text generation. Returns the response text."""
    # Translate Claude model names to Gemini equivalents
    if model.startswith("claude"):
        model = DEFAULT_MODEL

    payload: dict = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max_tokens},
    }
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    return _post(model, payload, retries)


def ask_fast(prompt: str, max_tokens: int = 512) -> str:
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
    """Call Gemini vision. Returns the response text."""
    if model.startswith("claude"):
        model = FAST_MODEL

    img_b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    payload: dict = {
        "contents": [
            {
                "parts": [
                    {"inline_data": {"mime_type": media_type, "data": img_b64}},
                    {"text": prompt},
                ]
            }
        ],
        "generationConfig": {"maxOutputTokens": max_tokens},
    }
    if system:
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    return _post(model, payload, retries)


def _post(model: str, payload: dict, retries: int) -> str:
    """POST to Gemini's generateContent endpoint with retry on 429/5xx.

    Backoff schedule: 30s, 60s, 120s — Gemini free tier is 15 RPM and the cool-down
    needs to be substantial enough to actually clear the bucket.
    """
    url = f"{_API_BASE}/{model}:generateContent?key={_api_key()}"
    backoff_seconds = [30, 60, 120, 240]
    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            r = httpx.post(url, json=payload, timeout=120)
            if r.status_code in (429, 503):
                wait = backoff_seconds[min(attempt, len(backoff_seconds) - 1)]
                logger.warning(f"[gemini] {r.status_code} — retrying in {wait}s (attempt {attempt + 1}/{retries})")
                time.sleep(wait)
                continue
            r.raise_for_status()
            data = r.json()
            try:
                return data["candidates"][0]["content"]["parts"][0]["text"]
            except (KeyError, IndexError) as e:
                # Sometimes Gemini returns a finishReason instead of content (safety filter, etc.)
                reason = data.get("candidates", [{}])[0].get("finishReason", "unknown")
                raise RuntimeError(f"Gemini returned no content (finishReason={reason}): {e}")
        except httpx.HTTPStatusError as e:
            last_err = e
            if e.response.status_code >= 500 and attempt < retries - 1:
                time.sleep(2 ** attempt * 2)
                continue
            raise
        except Exception as e:
            last_err = e
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise
    raise RuntimeError(f"Gemini call failed after {retries} retries: {last_err}")
