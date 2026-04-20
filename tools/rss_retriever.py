"""RSS retriever — feature-flagged extra signal source.

Complements the web-search cascade. Fetches a configured set of RSS feeds
once per run, filters entries by the research plan's inferred industry, and
returns matches in the same `{url, title, content, snippet}` shape as the
Tavily path so they merge into the retrieval bundle.

Enabled by setting PRISM_RETRIEVERS to a comma-separated list that includes
"rss". Feeds live in config/rss_feeds.yaml — grouped by industry so queries
only pull from domain-relevant feeds.

Zero-LLM. Uses `feedparser` (already transitively via httpx/requests-like
libs? actually no — kept lightweight by parsing feedparser ourselves via
httpx + simple XML). To avoid adding a dep, we use stdlib xml.etree.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import httpx
import yaml

logger = logging.getLogger(__name__)

_FEEDS_PATH = Path(__file__).resolve().parent.parent / "config" / "rss_feeds.yaml"
FRESHNESS_DAYS = 14   # only return entries newer than this
MAX_RESULTS_PER_FEED = 3


def is_enabled() -> bool:
    return "rss" in (os.environ.get("PRISM_RETRIEVERS") or "").lower().split(",")


@lru_cache(maxsize=1)
def _load_feeds() -> dict[str, list[str]]:
    try:
        with _FEEDS_PATH.open() as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


def _normalize(s: str) -> str:
    return (s or "").lower().replace("_", " ").replace("-", " ").strip()


def _industry_keys(inferred_industry: str) -> list[str]:
    """Map the planner's inferred_industry string to feed-bucket keys.

    Normalizes underscores and hyphens to spaces on both sides so "food_delivery"
    in the YAML matches "food delivery and quick commerce" from the planner.
    """
    ind_norm = _normalize(inferred_industry)
    feeds = _load_feeds()
    keys: list[str] = []
    for k in feeds:
        if k == "general":
            continue
        k_norm = _normalize(k)
        if k_norm == ind_norm or k_norm in ind_norm or ind_norm in k_norm:
            keys.append(k)
    if "general" in feeds:
        keys.append("general")
    return keys


def _parse_feed(xml_text: str) -> list[dict[str, Any]]:
    """Minimal RSS/Atom parser — returns [{title, url, content, published_at}]."""
    out: list[dict[str, Any]] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return out
    # RSS 2.0
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        desc = (item.findtext("description") or "").strip()
        pub = item.findtext("pubDate")
        out.append({"title": title, "url": url, "content": desc, "published_at": pub})
    # Atom
    atom = "{http://www.w3.org/2005/Atom}"
    for entry in root.iter(f"{atom}entry"):
        title = (entry.findtext(f"{atom}title") or "").strip()
        link_el = entry.find(f"{atom}link")
        url = (link_el.get("href") if link_el is not None else "") or ""
        summary = (entry.findtext(f"{atom}summary") or "").strip()
        if not summary:
            summary = (entry.findtext(f"{atom}content") or "").strip()
        pub = entry.findtext(f"{atom}updated") or entry.findtext(f"{atom}published")
        out.append({"title": title, "url": url, "content": summary, "published_at": pub})
    return out


def _is_fresh(pub: str | None) -> bool:
    if not pub:
        return True  # unknown → assume fresh
    for fmt in ("%a, %d %b %Y %H:%M:%S %Z", "%a, %d %b %Y %H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            dt = datetime.strptime(pub, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt > datetime.now(timezone.utc) - timedelta(days=FRESHNESS_DAYS)
        except ValueError:
            continue
    return True


def fetch_for_plan(inferred_industry: str, max_items: int = 12) -> list[dict[str, Any]]:
    """Return RSS entries relevant to `inferred_industry`. Empty if disabled."""
    if not is_enabled():
        return []

    keys = _industry_keys(inferred_industry)
    if not keys:
        return []
    feeds = _load_feeds()
    urls: list[str] = []
    for k in keys:
        urls.extend(feeds.get(k, []))
    if not urls:
        return []

    results: list[dict[str, Any]] = []
    with httpx.Client(timeout=10, follow_redirects=True) as client:
        for feed_url in urls:
            try:
                resp = client.get(feed_url)
                resp.raise_for_status()
                entries = _parse_feed(resp.text)
            except Exception as exc:
                logger.debug("[rss] feed %s failed: %s", feed_url, exc)
                continue
            kept = 0
            for e in entries:
                if kept >= MAX_RESULTS_PER_FEED:
                    break
                if not e.get("url") or not _is_fresh(e.get("published_at")):
                    continue
                results.append({
                    "url": e["url"],
                    "title": e["title"],
                    "content": e["content"][:2000],
                    "snippet": (e["content"][:300] if e.get("content") else ""),
                    "source": "rss",
                    "feed": feed_url,
                })
                kept += 1
            if len(results) >= max_items:
                break
    logger.info("[rss] industry=%r returned %d entries from %d feeds", inferred_industry, len(results), len(urls))
    return results[:max_items]
