"""Impact Analysis Agent --- maps 2nd and 3rd order effects of trends on companies.

Takes macro trends and traces causal chains:
  Trend -> 2nd order effects -> 3rd order impacts on specific companies

Example: "Rise of AI in travel"
  -> "OTAs investing in AI trip planners" (2nd order)
  -> "MMT's hotel partners in tech corridors lose corporate bookings" (3rd order)
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

from agent.base_autonomous_agent import AutonomousAgent
from utils.claude_client import ask
from webapp.api.models import Project, WorkItem

logger = logging.getLogger(__name__)


class ImpactAnalysisAgent(AutonomousAgent):
    """Autonomous agent that traces cascading effects of trends on competitors."""

    def __init__(self, project_id: int, db: Session):
        super().__init__("impact_analysis", project_id, db)

        # Load project info
        project = self.db.query(Project).filter(Project.id == project_id).first()
        self.project_name = project.name if project else "Unknown Project"
        self.project_description = project.description or "" if project else ""

    # ------------------------------------------------------------------
    # Abstract method implementations
    # ------------------------------------------------------------------

    def seed_backlog(self) -> list[dict]:
        """Return initial work items for impact analysis."""
        return [
            {
                "priority": 8,
                "category": "trend_cascade",
                "description": (
                    "Analyze all known trends and map their 2nd and 3rd order "
                    "effects on tracked competitors"
                ),
                "context_json": None,
            },
        ]

    def generate_next_work(self) -> list[dict]:
        """Use Claude to reason about which trends need deeper analysis or refresh."""
        summary = self.knowledge.get_knowledge_summary()

        # Gather existing entities
        trends = self.knowledge.find_entities(entity_type="trend")
        trend_names = [t["name"] for t in trends]

        effects = self.knowledge.find_entities(entity_type="effect")
        effect_names = [e["name"] for e in effects]

        competitors = self.knowledge.find_entities(entity_type="company")
        competitor_names = [c["name"] for c in competitors]

        # Gather completed work
        completed = (
            self.db.query(WorkItem)
            .filter(
                WorkItem.agent_type == self.agent_type,
                WorkItem.project_id == self.project_id,
                WorkItem.status == "completed",
            )
            .order_by(WorkItem.completed_at.desc())
            .limit(20)
            .all()
        )
        completed_descriptions = [
            f"[{w.category}] {w.description} -> {w.result_summary or 'done'}"
            for w in completed
        ]

        # Determine which trends lack mapped effects
        trends_with_effects: set[str] = set()
        for effect in effects:
            obs = self.knowledge.get_observations(effect["id"], limit=1)
            if obs:
                trends_with_effects.add(effect.get("name", ""))

        prompt = f"""You are a strategic impact analyst deciding what to analyze next.

Project: {self.project_name}
Known trends: {json.dumps(trend_names)}
Known 2nd order effects: {json.dumps(effect_names)}
Known competitors: {json.dumps(competitor_names)}

Already done:
{chr(10).join(completed_descriptions[-5:]) or "(none)"}

Pick 2-3 SPECIFIC next tasks. Prioritize:
1. Trends that have no mapped effects yet (trend_cascade)
2. Existing impacts that may need updating with new data (impact_refresh)
3. Cross-trend intersections where multiple trends amplify each other (cross_trend)

Return ONLY a JSON array. Each item:
{{"priority": 7-9, "category": "trend_cascade"|"impact_refresh"|"cross_trend", "description": "specific task", "context_json": {{"trend_name": "X"}} or {{"trend_names": ["A","B"]}}}}"""

        try:
            response = ask(prompt, max_tokens=2048)
            text = response.strip()
            # Handle markdown code blocks
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()
            items = json.loads(text)
            if isinstance(items, list) and len(items) > 0:
                return items
        except (json.JSONDecodeError, IndexError, Exception) as exc:
            logger.warning("Failed to parse Claude's work suggestions: %s", exc)

        # Fallback: generate sensible defaults
        fallback: list[dict] = []
        for name in trend_names[:3]:
            fallback.append({
                "priority": 7,
                "category": "trend_cascade",
                "description": f"Map 2nd and 3rd order effects of '{name}' on competitors",
                "context_json": {"trend_name": name},
            })
        if not fallback:
            fallback.append({
                "priority": 8,
                "category": "trend_cascade",
                "description": (
                    "No trends found yet. Analyze any available knowledge "
                    "to identify emerging trends and their cascading effects."
                ),
                "context_json": None,
            })
        return fallback

    def get_tools(self) -> list[dict]:
        """Return Anthropic-format tool schemas for the tool-use loop."""
        return [
            {
                "name": "query_trends",
                "description": "Get all trend entities from the knowledge graph.",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
            {
                "name": "query_competitors",
                "description": "Get all competitor (company) entities from the knowledge graph.",
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
            {
                "name": "create_effect",
                "description": (
                    "Create a 2nd order effect entity and link it to the trend that causes it."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Short name for the 2nd order effect.",
                        },
                        "description": {
                            "type": "string",
                            "description": "Detailed description of the effect.",
                        },
                        "caused_by_trend": {
                            "type": "string",
                            "description": "Name of the trend that causes this effect.",
                        },
                        "severity": {
                            "type": "string",
                            "description": "Severity level.",
                            "enum": ["high", "medium", "low"],
                        },
                        "timeframe": {
                            "type": "string",
                            "description": "Expected timeframe.",
                            "enum": ["near", "medium", "long"],
                        },
                    },
                    "required": ["name", "description", "caused_by_trend", "severity", "timeframe"],
                },
            },
            {
                "name": "create_impact",
                "description": (
                    "Create a 3rd order impact on a specific company, "
                    "linked to a 2nd order effect."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "effect_name": {
                            "type": "string",
                            "description": "Name of the 2nd order effect this stems from.",
                        },
                        "company_name": {
                            "type": "string",
                            "description": "Name of the company that is impacted.",
                        },
                        "impact_description": {
                            "type": "string",
                            "description": "Specific description of the impact on this company.",
                        },
                        "severity": {
                            "type": "string",
                            "description": "Severity level.",
                            "enum": ["high", "medium", "low"],
                        },
                        "timeframe": {
                            "type": "string",
                            "description": "Expected timeframe.",
                            "enum": ["near", "medium", "long"],
                        },
                        "is_threat": {
                            "type": "boolean",
                            "description": "True if this is a threat, false if an opportunity.",
                        },
                    },
                    "required": [
                        "effect_name",
                        "company_name",
                        "impact_description",
                        "severity",
                        "timeframe",
                        "is_threat",
                    ],
                },
            },
            {
                "name": "save_finding",
                "description": "Save a general research finding or observation about an entity.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "entity_name": {
                            "type": "string",
                            "description": "Name of the entity this finding is about.",
                        },
                        "content": {
                            "type": "string",
                            "description": "The finding content.",
                        },
                        "observation_type": {
                            "type": "string",
                            "description": "Type of observation.",
                            "enum": [
                                "impact_analysis",
                                "trend_signal",
                                "general",
                            ],
                        },
                        "source_url": {
                            "type": "string",
                            "description": "URL where this information was found.",
                        },
                        "lenses": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Analytical lenses: product_craft, growth, supply, "
                                "monetization, technology, brand_trust, moat, trajectory"
                            ),
                        },
                    },
                    "required": ["entity_name", "content", "observation_type"],
                },
            },
            {
                "name": "query_knowledge",
                "description": "Search existing knowledge in the graph.",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query.",
                        },
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "finish_work",
                "description": (
                    "Signal that the current work item is complete. "
                    "Call this when you have finished all analysis for the current task."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "summary": {
                            "type": "string",
                            "description": "Summary of what was accomplished.",
                        },
                        "entities_created": {
                            "type": "integer",
                            "description": "Number of new entities created.",
                        },
                        "observations_added": {
                            "type": "integer",
                            "description": "Number of observations added.",
                        },
                    },
                    "required": ["summary", "entities_created", "observations_added"],
                },
            },
        ]

    def get_system_prompt(self) -> str:
        """Return the system prompt for the Claude tool-use loop."""
        summary = self.knowledge.get_knowledge_summary()
        return (
            f"You are a Strategic Impact Analyst. Your job is to trace how macro trends "
            f"cascade into specific impacts on companies in the {self.project_name} market.\n\n"
            f"For each trend, reason through:\n"
            f"1. What are the direct, obvious consequences? (2nd order effects)\n"
            f"2. What do THOSE consequences cause for specific companies? (3rd order impacts)\n"
            f"3. Is each impact a THREAT or OPPORTUNITY for the company?\n"
            f"4. How severe (high/medium/low) and when "
            f"(near: <6mo, medium: 6-18mo, long: >18mo)?\n\n"
            f"RULES:\n"
            f"- Every effect and impact must be SPECIFIC, not generic\n"
            f'- BAD: "AI will disrupt travel" --- too vague\n'
            f'- GOOD: "AI trip planners reduce need for human travel agents, threatening '
            f"OTAs that monetize through agent-assisted bookings "
            f'(e.g., Yatra\'s corporate segment)"\n'
            f"- Always specify which company is impacted and why\n"
            f"- Use create_effect for 2nd order, create_impact for 3rd order\n"
            f"- Call finish_work within 10 tool calls\n\n"
            f"Current knowledge state:\n"
            f"{json.dumps(summary, indent=2, default=str)}"
        )

    def execute_tool(self, tool_name: str, tool_input: dict) -> str:
        """Dispatch tool calls to their implementations."""
        try:
            if tool_name == "query_trends":
                return self._tool_query_trends()

            elif tool_name == "query_competitors":
                return self._tool_query_competitors()

            elif tool_name == "create_effect":
                return self._tool_create_effect(tool_input)

            elif tool_name == "create_impact":
                return self._tool_create_impact(tool_input)

            elif tool_name == "save_finding":
                return self._tool_save_finding(tool_input)

            elif tool_name == "query_knowledge":
                return self._tool_query_knowledge(tool_input)

            elif tool_name == "finish_work":
                return self._tool_finish_work(tool_input)

            else:
                return f"ERROR: Unknown tool '{tool_name}'"

        except Exception as exc:
            logger.error("Tool %s failed: %s", tool_name, exc, exc_info=True)
            return f"ERROR: {exc}"

    def execute_work_item(self, item: WorkItem) -> dict:
        """Execute using efficient research (Groq free) — no tool-use loop."""
        self._current_result: dict = {
            "status": "completed",
            "summary": "Work item processed",
            "entities_created": 0,
            "observations_added": 0,
        }

        context = item.context_json or {}
        logger.info(f"[{self.agent_type}] Efficient impact analysis for: {item.category}")

        from agent.efficient_researcher import research_impact_cascade, _get_synthesizer

        # Get trends and competitors from knowledge graph
        trends = self.knowledge.find_entities(entity_type="trend")
        competitors = self.knowledge.find_entities(entity_type="company")
        trend_names = [t["name"] for t in trends]
        competitor_names = [c["name"] for c in competitors]

        if not trends:
            self._current_result["summary"] = "No trends found — run industry research first"
            return self._current_result

        # Process each trend
        total_effects = 0
        total_impacts = 0
        for trend in trends[:5]:  # Cap at 5 trends per session
            result = research_impact_cascade(
                trend["name"],
                trend.get("description", ""),
                competitor_names[:8],
                self.project_name,
            )

            # Save effects as entities
            for effect in result.get("effects", []):
                effect_id = self.knowledge.upsert_entity(
                    "effect", effect["name"], effect.get("description", ""),
                    metadata={"severity": effect.get("severity", "medium"), "timeframe": effect.get("timeframe", "medium")},
                )
                self.knowledge.add_relation(trend["id"], effect_id, "causes")
                total_effects += 1

                # Save impacts on companies
                for impact in result.get("impacts", []):
                    if impact.get("effect") == effect["name"]:
                        company_entities = self.knowledge.find_entities(name_like=impact.get("company", ""))
                        if company_entities:
                            self.knowledge.add_relation(
                                effect_id, company_entities[0]["id"], "impacts",
                                metadata={"severity": impact.get("severity", "medium"), "is_threat": impact.get("is_threat", True)},
                            )
                            self.knowledge.add_observation(
                                company_entities[0]["id"], "impact_analysis",
                                impact.get("description", ""), lens_tags=["trajectory"],
                            )
                            total_impacts += 1

        self._current_result = {
            "status": "completed",
            "summary": f"Mapped {total_effects} effects, {total_impacts} company impacts across {min(len(trends), 5)} trends",
            "entities_created": total_effects,
            "observations_added": total_impacts,
        }
        return self._current_result

        return self._current_result

    # ------------------------------------------------------------------
    # Tool implementations (private)
    # ------------------------------------------------------------------

    def _tool_query_trends(self) -> str:
        """Return all trend entities from the knowledge graph."""
        trends = self.knowledge.find_entities(entity_type="trend")
        # Enrich with observation counts
        for trend in trends:
            obs = self.knowledge.get_observations(trend["id"], limit=100)
            trend["observation_count"] = len(obs)
        return json.dumps(trends, default=str)

    def _tool_query_competitors(self) -> str:
        """Return all competitor (company) entities from the knowledge graph."""
        competitors = self.knowledge.find_entities(entity_type="company")
        for comp in competitors:
            obs = self.knowledge.get_observations(comp["id"], limit=100)
            comp["observation_count"] = len(obs)
        return json.dumps(competitors, default=str)

    def _tool_create_effect(self, inp: dict) -> str:
        """Create a 2nd order effect entity and link it to its causal trend."""
        # 1. Create the effect entity
        effect_id = self.knowledge.upsert_entity(
            entity_type="effect",
            name=inp["name"],
            description=inp["description"],
            metadata={
                "severity": inp["severity"],
                "timeframe": inp["timeframe"],
            },
        )

        # 2. Find the trend entity and link it
        trends = self.knowledge.find_entities(
            entity_type="trend", name_like=inp["caused_by_trend"]
        )
        if trends:
            trend_id = trends[0]["id"]
            self.knowledge.add_relation(
                from_id=trend_id,
                to_id=effect_id,
                relation_type="causes",
            )
        else:
            logger.warning(
                "Trend '%s' not found in knowledge graph; effect created without link",
                inp["caused_by_trend"],
            )

        # 3. Add observation on the effect entity
        self.knowledge.add_observation(
            entity_id=effect_id,
            obs_type="impact_analysis",
            content=inp["description"],
        )

        self._current_result["entities_created"] = (
            self._current_result.get("entities_created", 0) + 1
        )

        return json.dumps({
            "status": "created",
            "effect_entity_id": effect_id,
            "name": inp["name"],
            "linked_to_trend": bool(trends),
        })

    def _tool_create_impact(self, inp: dict) -> str:
        """Create a 3rd order impact linking an effect to a company."""
        # 1. Find the effect entity
        effects = self.knowledge.find_entities(
            entity_type="effect", name_like=inp["effect_name"]
        )
        if not effects:
            return json.dumps({
                "status": "error",
                "message": f"Effect '{inp['effect_name']}' not found. Create it first with create_effect.",
            })
        effect_id = effects[0]["id"]

        # 2. Find the company entity
        companies = self.knowledge.find_entities(
            entity_type="company", name_like=inp["company_name"]
        )
        if not companies:
            return json.dumps({
                "status": "error",
                "message": f"Company '{inp['company_name']}' not found in knowledge graph.",
            })
        company_id = companies[0]["id"]

        # 3. Add relation from effect to company
        self.knowledge.add_relation(
            from_id=effect_id,
            to_id=company_id,
            relation_type="impacts",
            metadata={
                "severity": inp["severity"],
                "timeframe": inp["timeframe"],
                "is_threat": inp["is_threat"],
            },
        )

        # 4. Add observation on the company entity
        obs_id = self.knowledge.add_observation(
            entity_id=company_id,
            obs_type="impact_analysis",
            content=inp["impact_description"],
            lens_tags=["trajectory"],
        )

        self._current_result["observations_added"] = (
            self._current_result.get("observations_added", 0) + 1
        )

        impact_type = "THREAT" if inp["is_threat"] else "OPPORTUNITY"
        return json.dumps({
            "status": "created",
            "impact_type": impact_type,
            "effect": inp["effect_name"],
            "company": inp["company_name"],
            "observation_id": obs_id,
        })

    def _tool_save_finding(self, inp: dict) -> str:
        """Save an observation about an entity."""
        # Find the entity by name
        entities = self.knowledge.find_entities(name_like=inp["entity_name"])
        if not entities:
            # Create a generic entity if not found
            entity_id = self.knowledge.upsert_entity(
                entity_type="company",
                name=inp["entity_name"],
                description="Auto-created from finding",
            )
        else:
            entity_id = entities[0]["id"]

        obs_id = self.knowledge.add_observation(
            entity_id=entity_id,
            obs_type=inp["observation_type"],
            content=inp["content"],
            source_url=inp.get("source_url"),
            lens_tags=inp.get("lenses"),
        )

        self._current_result["observations_added"] = (
            self._current_result.get("observations_added", 0) + 1
        )

        return json.dumps({
            "status": "saved",
            "observation_id": obs_id,
            "entity_id": entity_id,
        })

    def _tool_query_knowledge(self, inp: dict) -> str:
        """Query the knowledge graph for existing information."""
        results: dict[str, Any] = {}

        # Search entities
        entities = self.knowledge.find_entities(name_like=inp["query"])
        results["entities"] = entities

        # Semantic search for related observations
        semantic = self.knowledge.semantic_search(inp["query"], top_k=5)
        results["semantic_matches"] = semantic

        # If we found entities, include their recent observations
        if entities:
            for ent in entities[:3]:
                obs = self.knowledge.get_observations(ent["id"], limit=5)
                ent["recent_observations"] = obs

        return json.dumps(results, default=str)

    def _tool_finish_work(self, inp: dict) -> str:
        """Mark the current work item as complete."""
        self._current_result = {
            "status": "completed",
            "summary": inp["summary"],
            "entities_created": inp.get("entities_created", 0),
            "observations_added": inp.get("observations_added", 0),
        }
        return json.dumps({
            "status": "completed",
            "summary": inp["summary"],
        })

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_work_prompt(
        self, category: str, description: str, context: dict
    ) -> str:
        """Build a targeted prompt based on work item category."""
        if category == "trend_cascade":
            # Gather current trends and competitors for context
            trends = self.knowledge.find_entities(entity_type="trend")
            trend_names = [t["name"] for t in trends]
            competitors = self.knowledge.find_entities(entity_type="company")
            competitor_names = [
                c["name"] for c in competitors if c["name"] != self.project_name
            ]

            return (
                f"Analyze the following trends and map their 2nd and 3rd order effects "
                f"on these competitors:\n\n"
                f"Trends: {json.dumps(trend_names) if trend_names else '(use query_trends to discover)'}\n"
                f"Competitors: {json.dumps(competitor_names) if competitor_names else '(use query_competitors to discover)'}\n\n"
                f"For each trend:\n"
                f"1. Use create_effect for each 2nd order consequence\n"
                f"2. Use create_impact for each company-specific 3rd order impact\n"
                f"3. Be SPECIFIC about which company and why\n\n"
                f"Call finish_work when done."
            )

        elif category == "impact_refresh":
            return (
                f"Re-evaluate existing impacts based on recent observations. "
                f"Check if any impacts have changed severity or new ones emerged.\n\n"
                f"Steps:\n"
                f"1. Use query_trends and query_competitors to see current state\n"
                f"2. Use query_knowledge to check recent observations\n"
                f"3. If severities have changed, create updated effects/impacts\n"
                f"4. If new cascading effects emerged, map them\n"
                f"5. Call finish_work with a summary of changes\n\n"
                f"Only create NEW effects or impacts --- do not duplicate existing ones."
            )

        elif category == "cross_trend":
            return (
                f"Look for intersections where multiple trends amplify each other's "
                f"effects on the same companies.\n\n"
                f"Steps:\n"
                f"1. Use query_trends to see all trends\n"
                f"2. Use query_knowledge to find existing effects and impacts\n"
                f"3. Identify where 2+ trends converge on the same company or market segment\n"
                f"4. Create effects and impacts for these amplified intersections\n"
                f"5. Use save_finding to document the cross-trend interaction\n"
                f"6. Call finish_work\n\n"
                f"Focus on compound effects that are non-obvious from looking at "
                f"trends individually."
            )

        else:
            return (
                f"{description}\n\n"
                f"Be specific. Map 2nd order effects with create_effect, "
                f"3rd order impacts with create_impact. "
                f"Call finish_work within 10 tool calls."
            )
