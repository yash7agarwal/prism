"""Efficient research module — deterministic search + single LLM synthesis.

Instead of 10-15 LLM calls per work item (tool-use loop), this module:
1. Runs web searches deterministically (no LLM needed to decide what to search)
2. Fetches and extracts content from top results (no LLM needed)
3. Makes ONE LLM call to synthesize findings from the raw data
4. Saves structured results to the knowledge graph

Cost: 1-2 LLM calls per work item instead of 10-15.
Provider: Groq (free Llama 3.1) > Claude > Gemini fallback chain.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from tools.web_research import WebResearcher

logger = logging.getLogger(__name__)

_web = WebResearcher()


def _get_synthesizer():
    """Get the best available synthesis function. Priority: Groq > Claude > Gemini."""
    try:
        from utils.groq_client import synthesize, is_available
        if is_available():
            logger.info("[researcher] Using Groq (free) for synthesis")
            return synthesize
    except ImportError:
        pass

    try:
        from utils.claude_client import ask
        logger.info("[researcher] Using Claude for synthesis")
        return ask
    except Exception:
        pass

    from utils.gemini_client import ask
    logger.info("[researcher] Using Gemini for synthesis")
    return ask


def research_competitor(
    competitor_name: str,
    project_name: str,
    project_description: str,
) -> dict:
    """Research a competitor using deterministic search + single synthesis.

    Returns: {
        "findings": [{"type": str, "content": str, "lenses": [str], "source_url": str}],
        "profile_md": str,  # markdown profile report
        "entities_created": int,
        "observations_added": int,
    }
    """
    synthesize = _get_synthesizer()

    # Step 1: Deterministic searches (no LLM needed)
    searches = [
        f"{competitor_name} revenue annual report 2025 2026",
        f"{competitor_name} new features launches 2025 2026",
        f"{competitor_name} pricing commission fees",
        f"{competitor_name} app rating reviews Play Store",
        f"{competitor_name} acquisitions partnerships funding 2025 2026",
        f"{competitor_name} market share users downloads",
    ]

    raw_data = []
    sources = []
    for query in searches:
        results = _web.search(query, max_results=3)
        for r in results[:2]:  # Top 2 per search
            page = _web.fetch_page(r.get("url", ""), max_length=5000)
            if page.get("content") and len(page["content"]) > 100:
                raw_data.append({
                    "query": query,
                    "url": r.get("url", ""),
                    "title": r.get("title", ""),
                    "content": page["content"][:3000],
                })
                sources.append(r.get("url", ""))

    if not raw_data:
        return {"findings": [], "profile_md": "", "entities_created": 0, "observations_added": 0}

    # Step 2: ONE synthesis call
    raw_text = "\n\n---\n\n".join([
        f"Source: {d['url']}\nTitle: {d['title']}\n{d['content']}"
        for d in raw_data[:8]  # Cap at 8 sources to fit context
    ])

    synthesis_prompt = f"""Analyze the following raw research data about {competitor_name} (a competitor of {project_name}).

{project_name} description: {project_description}

RAW DATA:
{raw_text}

Extract SPECIFIC findings. For each finding, provide:
- "type": one of "metric", "feature_change", "pricing_update", "news", "general"
- "content": the specific finding (2-3 sentences, include numbers/dates)
- "lenses": array of 1-3 from: product_craft, growth, supply, monetization, technology, brand_trust, moat, trajectory
- "source_url": the URL this came from

Return a JSON object:
{{
    "findings": [
        {{"type": "metric", "content": "...", "lenses": ["growth", "monetization"], "source_url": "..."}},
        ...
    ],
    "summary": "One paragraph executive summary of this competitor's position"
}}

Rules:
- Extract 4-8 findings, each SPECIFIC with numbers/dates
- Every finding MUST have a source_url from the raw data
- Do NOT invent facts not present in the raw data
- Focus on: revenue/financials, recent product changes, pricing model, strategic moves, app metrics"""

    try:
        response = synthesize(
            synthesis_prompt,
            max_tokens=3000,
            system="You are a competitive intelligence analyst. Extract specific, evidence-backed findings from raw research data. Return valid JSON only.\n\nCRITICAL: Do NOT invent facts, numbers, or percentages not present in the raw data. If data is unavailable, say 'data not available'. Every claim must cite which source it came from. Fabricating data is strictly prohibited.",
        )

        # Parse response
        text = response.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        result = json.loads(text)
        findings = result.get("findings", [])
        summary = result.get("summary", "")

        return {
            "findings": findings,
            "profile_md": f"# {competitor_name} — Competitive Profile\n\n{summary}\n\n## Key Findings\n\n" +
                          "\n".join([f"- **[{f['type']}]** {f['content']}" for f in findings]),
            "entities_created": 0,
            "observations_added": len(findings),
        }

    except (json.JSONDecodeError, Exception) as e:
        logger.error(f"[researcher] Synthesis failed for {competitor_name}: {e}")
        return {"findings": [], "profile_md": "", "entities_created": 0, "observations_added": 0}


def research_industry_trends(
    project_name: str,
    project_description: str,
    known_trends: list[str] | None = None,
) -> dict:
    """Research consumer behavior trends, niche segments, and emerging needs.

    Focuses on JTBD (Jobs To Be Done), underserved segments, and behavioral
    shifts — NOT macro industry facts that everyone already knows.
    """
    synthesize = _get_synthesizer()

    # Searches targeted at CONSUMER BEHAVIOR and NICHE SEGMENTS, not macro facts
    searches = [
        f"{project_name} consumer behavior trends 2025 2026 new needs",
        f"solo travel women safety pet friendly travel trends India 2025",
        f"Gen Z millennial travel preferences booking behavior 2025",
        f"bleisure workation micro-trip spontaneous booking trend 2025",
        f"sustainable travel eco tourism accessibility travel trend",
        f"BNPL travel buy now pay later subscription travel 2025",
        f"voice search travel booking AI concierge personalization",
        f"{project_name} unmet customer needs complaints gaps",
    ]

    raw_data = []
    for query in searches:
        results = _web.search(query, max_results=3)
        for r in results[:2]:
            page = _web.fetch_page(r.get("url", ""), max_length=4000)
            if page.get("content") and len(page["content"]) > 100:
                raw_data.append({
                    "url": r.get("url", ""),
                    "title": r.get("title", ""),
                    "content": page["content"][:2500],
                })

    if not raw_data:
        return {"trends": []}

    raw_text = "\n\n---\n\n".join([
        f"Source: {d['url']}\nTitle: {d['title']}\n{d['content']}"
        for d in raw_data[:8]
    ])

    known = f"\nAlready known (skip these): {', '.join(known_trends)}" if known_trends else ""

    synthesis_prompt = f"""You are a consumer insights researcher for {project_name}.
{project_description}
{known}

RAW DATA:
{raw_text}

Your job: identify NICHE CONSUMER TRENDS that reveal new product opportunities.

DO NOT extract:
- "Travel market is growing" — everyone knows this
- "Mobile bookings increasing" — obvious
- "AI is transforming travel" — too broad
- Generic industry size/growth numbers

DO extract:
- Specific underserved segments with unmet needs (e.g., "Women solo travelers need verified safe stays — 78% cite safety as top concern per Booking.com 2025 survey")
- Behavioral shifts that create new JTBD (e.g., "Bleisure travelers need split-billing between personal and corporate cards — no OTA supports this natively")
- Emerging categories with quantified demand (e.g., "Pet-friendly hotel searches up 340% YoY on Google India, but only 2% of OTA listings are pet-tagged")
- Regulatory changes creating new needs (e.g., "New GST rules for hotel aggregators effective Jan 2026 require room-level tax breakdowns at checkout")

For each trend, provide:
- "name": specific name (NOT "Growing travel market")
- "description": 3-4 sentences explaining the consumer need, why it's underserved, and what a product could do about it
- "timeline": "past" (happened) | "present" (happening now) | "emerging" (early signals) | "future" (predicted)
- "category": "consumer_behavior" | "technology" | "regulation" | "demographics" | "market_structure"
- "quantification": {{"search_volume": "X/mo", "market_size": "$X", "growth_rate": "X%", "user_demand": "X% cite this"}} — include whatever data exists
- "jtbd": what job is the customer trying to get done?
- "product_opportunity": what could {project_name} build to address this?
- "source_url": from the raw data

Return JSON: {{"trends": [...]}}

Extract 6-10 trends. Every trend must be SPECIFIC and ACTIONABLE — a PM should read it and immediately see a product opportunity."""

    try:
        response = synthesize(
            synthesis_prompt,
            max_tokens=4096,
            system="You are a consumer insights researcher. Extract specific, niche, actionable consumer trends — NOT obvious industry facts. Return valid JSON only.\n\nCRITICAL: Do NOT invent facts, numbers, or percentages not present in the raw data. If data is unavailable, say 'data not available'. Every claim must cite which source it came from. Fabricating data is strictly prohibited.",
        )

        text = response.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        return json.loads(text)

    except (json.JSONDecodeError, Exception) as e:
        logger.error(f"[researcher] Trend synthesis failed: {e}")
        return {"trends": []}


def research_impact_cascade(
    trend_name: str,
    trend_description: str,
    competitor_names: list[str],
    project_name: str,
) -> dict:
    """Analyze 2nd and 3rd order impacts using deep causal reasoning.

    3rd order thinking explained:
    - 1st order: The trend itself (e.g., "Rise of AI in travel")
    - 2nd order: Direct consequences (e.g., "OTAs build AI trip planners")
    - 3rd order: Non-obvious downstream effects (e.g., "AI planners recommend
      off-beat destinations → boutique hotels in Tier-3 cities see a significant
      booking increase → MMT's hotel supply team needs to rapidly onboard new
      properties in small towns to capture this demand")

    The 3rd order is where the strategic insight lives — it's what competitors
    DON'T see coming.
    """
    synthesize = _get_synthesizer()

    # Search for real-world examples of this trend's effects
    search_results = _web.search(f'"{trend_name}" impact consequences effects 2025 2026', max_results=3)
    context = ""
    for r in search_results[:2]:
        page = _web.fetch_page(r.get("url", ""), max_length=2000)
        if page.get("content") and len(page["content"]) > 100:
            context += f"\nSource: {r.get('url','')}\n{page['content'][:1500]}\n---\n"

    prompt = f"""You are a strategic analyst who thinks in 3rd order effects.

TREND: {trend_name}
DESCRIPTION: {trend_description}
COMPANIES: {', '.join(competitor_names)}
OUR COMPANY: {project_name}

{f"REAL-WORLD CONTEXT:{context}" if context else ""}

3RD ORDER THINKING EXPLAINED:
Most analysts stop at 2nd order: "AI → companies build AI features." That's obvious.
3rd order asks: "And THEN what happens?"

Example of good 3rd order chain:
- Trend: "Rise of AI trip planners"
- 2nd order: "OTAs integrate AI for personalized recommendations"
- 3rd order: "AI planners recommend off-beat destinations based on user preferences →
  demand shifts from top-10 tourist cities to Tier-3 towns → hotels in small towns
  see a significant booking increase → {project_name}'s hotel supply team needs to
  urgently onboard new properties in small towns OR lose this demand to Airbnb which
  already has home-stay inventory there"

That 3rd order effect is ACTIONABLE — it tells the PM exactly what to build next.

BAD (generic, obvious):
- "AI will increase competition" — so what?
- "Companies will invest in technology" — meaningless
- "Users will benefit from better experience" — not actionable

GOOD (specific, non-obvious, actionable):
- "AI concierge substantially reduces post-booking support calls → {project_name}'s
  large call center becomes a cost liability → restructure to AI-first support yields
  major savings but requires a long migration timeline"

For each effect chain, think: "What does this mean the PM should BUILD or STOP BUILDING?"

Return JSON:
{{
    "effects": [
        {{
            "name": "specific non-obvious effect",
            "description": "3-4 sentences explaining the causal chain and WHY this matters",
            "severity": "high|medium|low",
            "timeframe": "near (<6mo)|medium (6-18mo)|long (>18mo)",
            "what_to_build": "specific product action this implies"
        }}
    ],
    "impacts": [
        {{
            "effect": "effect name from above",
            "company": "specific company name",
            "description": "How THIS specific company is affected, with numbers if possible. What do they lose or gain?",
            "is_threat": true/false,
            "severity": "high|medium|low",
            "strategic_implication": "What should {project_name} do about this?"
        }}
    ]
}}

Extract 3-5 effects with 2-3 company impacts each. Every effect must pass the "so what?" test — if a PM reads it and shrugs, it's not specific enough."""

    try:
        response = synthesize(
            prompt,
            max_tokens=3000,
            system="You are a strategic analyst specializing in 3rd order effects. Think non-obviously. Every insight must be specific enough that a PM can act on it. Return valid JSON only.\n\nCRITICAL: Do NOT invent facts, numbers, or percentages not present in the raw data. If data is unavailable, say 'data not available'. Every claim must cite which source it came from. Fabricating data is strictly prohibited.",
        )

        text = response.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()

        return json.loads(text)

    except (json.JSONDecodeError, Exception) as e:
        logger.error(f"[researcher] Impact cascade failed: {e}")
        return {"effects": [], "impacts": []}
