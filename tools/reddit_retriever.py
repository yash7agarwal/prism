"""Reddit retriever — feature-flagged signal source for niche conversation.

Appends `.json` to any public subreddit URL to get structured posts without
auth. Returns results in the same `{url, title, content, snippet}` shape as
the Tavily path so they merge into the retrieval bundle.

Enabled via PRISM_RETRIEVERS env var containing "reddit". Subreddit map in
config/reddit_subreddits.yaml.

Rate-limit friendly: Reddit allows ~60 req/min for unauth UAs. We make at
most 3 calls per run and cache sessions where possible.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx
import yaml

logger = logging.getLogger(__name__)

_SUBS_PATH = Path(__file__).resolve().parent.parent / "config" / "reddit_subreddits.yaml"
MIN_UPVOTES = 10           # filter noise
MAX_RESULTS_PER_SUB = 5
_USER_AGENT = "prism-research/0.13 (+https://github.com/yash7agarwal/prism)"


def is_enabled() -> bool:
    return "reddit" in (os.environ.get("PRISM_RETRIEVERS") or "").lower().split(",")


@lru_cache(maxsize=1)
def _load_subs() -> dict[str, list[str]]:
    try:
        with _SUBS_PATH.open() as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


def _normalize(s: str) -> str:
    return (s or "").lower().replace("_", " ").replace("-", " ").strip()


def _industry_subs(inferred_industry: str) -> list[str]:
    ind_norm = _normalize(inferred_industry)
    subs_by_ind = _load_subs()
    keys: list[str] = []
    for k in subs_by_ind:
        if k == "general":
            continue
        k_norm = _normalize(k)
        if k_norm == ind_norm or k_norm in ind_norm or ind_norm in k_norm:
            keys.append(k)
    if "general" in subs_by_ind:
        keys.append("general")
    out: list[str] = []
    for k in keys:
        out.extend(subs_by_ind.get(k, []))
    # Preserve order, dedupe.
    seen: set[str] = set()
    uniq: list[str] = []
    for s in out:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq


def fetch_for_plan(inferred_industry: str, query_hint: str = "", max_items: int = 12) -> list[dict[str, Any]]:
    """Return top posts from industry-relevant subs."""
    if not is_enabled():
        return []
    subs = _industry_subs(inferred_industry)
    if not subs:
        return []

    results: list[dict[str, Any]] = []
    with httpx.Client(timeout=10, follow_redirects=True, headers={"User-Agent": _USER_AGENT}) as client:
        for sub in subs[:4]:
            url = f"https://www.reddit.com/r/{sub}/top.json?t=month&limit=10"
            try:
                resp = client.get(url)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                logger.debug("[reddit] /r/%s fetch failed: %s", sub, exc)
                continue
            kept = 0
            for child in data.get("data", {}).get("children", []):
                if kept >= MAX_RESULTS_PER_SUB:
                    break
                post = child.get("data", {})
                if post.get("score", 0) < MIN_UPVOTES:
                    continue
                permalink = post.get("permalink")
                if not permalink:
                    continue
                title = post.get("title") or ""
                body = post.get("selftext") or ""
                results.append({
                    "url": f"https://www.reddit.com{permalink}",
                    "title": title,
                    "content": (body[:2000] if body else title),
                    "snippet": (body[:300] if body else title),
                    "source": "reddit",
                    "subreddit": sub,
                    "score": post.get("score", 0),
                })
                kept += 1
            if len(results) >= max_items:
                break
    logger.info(
        "[reddit] industry=%r returned %d posts from %d subs",
        inferred_industry, len(results), len(subs),
    )
    return results[:max_items]
