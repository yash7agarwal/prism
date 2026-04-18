"""Groq client — free Llama 3.1 inference for Prism agents.

Groq free tier: 14,400 RPD, 30 RPM for Llama 3.1 70B.
No tool-use needed — we use structured prompts and JSON parsing instead.
This is the primary provider for cost-efficient agent synthesis.
"""
from __future__ import annotations

import json
import logging
import os
import time

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "llama-3.3-70b-versatile"
FAST_MODEL = "llama-3.1-8b-instant"

_API_BASE = "https://api.groq.com/openai/v1/chat/completions"


def _api_key() -> str:
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise RuntimeError(
            "GROQ_API_KEY not set. Get one free at https://console.groq.com/keys"
        )
    return key


def synthesize(
    prompt: str,
    max_tokens: int = 4096,
    model: str = DEFAULT_MODEL,
    system: str = "",
    retries: int = 3,
) -> str:
    """Call Groq for text synthesis. Returns the response text.

    Use this for the heavy lifting — analyzing raw research data
    and producing structured findings. Free and fast.
    """
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.3,
    }

    headers = {
        "Authorization": f"Bearer {_api_key()}",
        "Content-Type": "application/json",
    }

    backoff = [5, 15, 30]
    for attempt in range(retries):
        try:
            r = httpx.post(_API_BASE, json=payload, headers=headers, timeout=60)
            if r.status_code == 429:
                wait = backoff[min(attempt, len(backoff) - 1)]
                logger.warning(f"[groq] 429 rate limit — retrying in {wait}s")
                time.sleep(wait)
                continue
            r.raise_for_status()
            data = r.json()
            return data["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500 and attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            raise

    raise RuntimeError(f"Groq call failed after {retries} retries")


def synthesize_json(
    prompt: str,
    max_tokens: int = 4096,
    model: str = DEFAULT_MODEL,
    system: str = "",
) -> dict | list:
    """Call Groq and parse the response as JSON.

    Adds JSON instruction to the prompt and handles parsing.
    """
    full_prompt = f"{prompt}\n\nRespond with ONLY valid JSON, no other text."

    text = synthesize(full_prompt, max_tokens=max_tokens, model=model, system=system)

    # Extract JSON from response (handle markdown code blocks)
    text = text.strip()
    if "```" in text:
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    return json.loads(text)


def is_available() -> bool:
    """Check if Groq API key is configured."""
    return bool(os.environ.get("GROQ_API_KEY"))
