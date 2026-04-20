"""Gemini text embeddings via REST (no SDK dep).

Model: `text-embedding-004` — 768 dim, free-tier enabled.

Used by agent/semantic_dedupe.py as an optional layer beneath the existing
trigram dedupe. Never raises — returns None on failure so the caller can
silently fall back.
"""
from __future__ import annotations

import logging
import os

import httpx
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

MODEL = "text-embedding-004"
_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
DIM = 768


def _api_key() -> str | None:
    return os.environ.get("GEMINI_API_KEY")


def embed(text: str, task_type: str = "SEMANTIC_SIMILARITY") -> list[float] | None:
    """Compute an embedding vector for `text`. Returns None on any failure."""
    if not text or not text.strip():
        return None
    key = _api_key()
    if not key:
        return None
    payload = {
        "model": f"models/{MODEL}",
        "content": {"parts": [{"text": text[:2000]}]},
        "taskType": task_type,
    }
    url = f"{_API_BASE}/{MODEL}:embedContent?key={key}"
    try:
        resp = httpx.post(url, json=payload, timeout=15)
        if resp.status_code == 429:
            logger.debug("[gemini_embed] 429 — skipping")
            return None
        resp.raise_for_status()
        data = resp.json()
        return data.get("embedding", {}).get("values")
    except Exception as exc:
        logger.debug("[gemini_embed] failed: %s", exc)
        return None


def is_available() -> bool:
    return bool(_api_key())
