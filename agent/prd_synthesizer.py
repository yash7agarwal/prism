"""PRD/Insights synthesizer — binds Prism's market lens to Loupe's build lens.

Produces a single Markdown document per feature that answers:
  - What does the market do here (Prism)?
  - What have we designed and built (Loupe)?
  - Where are the gaps?
  - What should we do next?

Flow:
  1. Build the project's ResearchBrief (reuses v0.11 spine)
  2. Pull Prism KG entities matching the feature name (competitors, trends,
     observations) via `KnowledgeStore.find_entities(name_like=feature)`
  3. Pull Loupe UAT evidence via `utils/loupe_client.fetch_feature_evidence`
  4. Build a strict evidence bundle for the LLM
  5. Single Sonnet call → Markdown following the PRD shape in the plan
  6. Save as `KnowledgeArtifact(artifact_type='prd_doc')` — queryable via
     the existing `/api/knowledge/artifacts/{id}` endpoint.

Graceful degradation:
  - No matching Prism entities → PRD section 1 says "no market context yet"
  - No matching Loupe runs → PRD section 2 says "no UAT coverage for this feature"
  - Loupe unreachable → PRD section 2 says "design evidence unavailable"
  - LLM provider exhausted → falls back via claude_client cascade
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from agent.knowledge_store import KnowledgeStore
from agent.research_brief import build_brief
from utils import claude_client, loupe_client
from webapp.api.models import Project

logger = logging.getLogger(__name__)

PRD_ARTIFACT_TYPE = "prd_doc"
MAX_ENTITIES_PER_TYPE = 10  # cap Prism evidence block
MAX_RUNS_IN_PROMPT = 3       # already capped by loupe_client, belt-and-braces


PRD_SYSTEM_PROMPT = """You are a product-intelligence synthesizer for Prism.

You produce strict Markdown PRDs that combine market intelligence (Prism KG)
with build-verification evidence (Loupe UAT). You never invent data. Every
claim must cite its source: a Prism entity ID or a Loupe run/frame ID.

If an evidence block is empty, say so explicitly — "No market context found"
or "No UAT coverage for this feature" — rather than filling the section with
generic observations.

Output MUST match this shape exactly:

# PRD: {feature} — {project}
_generated {timestamp} · sources: Prism KG + Loupe UAT_

## Executive summary
One paragraph: market context, current build status vs design, top 3 recommendations.

## 1. Market context (Prism)
- **Competitors**: one line per competitor, evidence-cited
- **Trends**: industry shifts affecting this feature
- **Regulatory / platform signals** (if any)
- **User feedback** (starred/dismissed items for this feature)

## 2. Designed vs. built (Loupe)
- **Figma frames**: count + file name
- **UAT run verdict**: MATCHES / DIFFERS / UNREACHABLE / ERROR
- **Per-frame match scores**: top 3 mismatches
- **Defects flagged**: specific issues
- Or explicit "No UAT coverage for this feature" callout

## 3. Gaps
- Competitor capability we lack
- Design-built drift
- Market trends not addressed in our flow
- Features we've designed but haven't UAT'd

## 4. Recommendations
Ranked list. Each item: what to build/fix, why, effort estimate (S/M/L), source tag.

## Sources
- Prism: entity IDs referenced
- Loupe: run IDs + frame IDs referenced"""


def _format_prism_evidence(entities: list[dict], observations: dict[int, list[dict]]) -> str:
    """Turn Prism entity+observation dicts into a compact evidence block."""
    if not entities:
        return "(no matching Prism entities — consider running industry_research on this project first)"

    by_type: dict[str, list[dict]] = {}
    for e in entities:
        by_type.setdefault(e["entity_type"], []).append(e)

    lines: list[str] = []
    for t in ("competitor", "company", "trend", "regulation"):
        items = by_type.get(t, [])
        if not items:
            continue
        lines.append(f"### {t}s ({len(items)})")
        for e in items[:MAX_ENTITIES_PER_TYPE]:
            lines.append(f"- [{e['id']}] **{e['name']}**  confidence={e.get('confidence', 1.0):.2f}")
            if e.get("description"):
                lines.append(f"  {e['description'][:300]}")
            if e.get("user_signal"):
                lines.append(f"  _user signal_: {e['user_signal']}"
                              + (f" — {e.get('dismissed_reason')}" if e.get("dismissed_reason") else ""))
            for obs in (observations.get(e["id"], []) or [])[:2]:
                src = obs.get("source_url") or ""
                lines.append(f"  · obs[{obs['id']}]: {obs['content'][:180]} {src}")
    return "\n".join(lines) or "(no matching Prism entities)"


def _format_loupe_evidence(bundle: dict[str, Any]) -> str:
    """Turn Loupe's fetch_feature_evidence bundle into a compact evidence block."""
    if not bundle.get("available", False):
        return "(Loupe unreachable — design/build evidence unavailable for this run)"

    matched = bundle.get("matched_runs", [])
    total = bundle.get("total_runs_for_project", 0)
    figma = bundle.get("figma_imports", [])

    if not matched:
        return (
            f"(no UAT runs matched this feature — project has {total} total UAT runs, "
            f"none with a matching feature_description)"
        )

    lines = [f"### Matched UAT runs: {len(matched)} of {total}"]
    for run in matched[:MAX_RUNS_IN_PROMPT]:
        lines.append(
            f"- run[{run.get('id')}] status={run.get('status')} "
            f"verdict_score={run.get('overall_match_score')} "
            f"frames total={run.get('total_frames')} matched={run.get('matched')} "
            f"differs={run.get('mismatched')} unreachable={run.get('unreachable')}"
        )
        if run.get("feature_description"):
            lines.append(f"  feature: {run['feature_description'][:200]}")
        if run.get("error"):
            lines.append(f"  error: {run['error'][:180]}")
        # Top 3 mismatches (if the run detail includes frame_results)
        frames = run.get("frame_results") or run.get("frames") or []
        mismatches = [f for f in frames if (f.get("verdict") or "").upper() == "DIFFERS"][:3]
        for f in mismatches:
            issues = f.get("issues") or []
            issues_str = ", ".join(str(i)[:80] for i in issues[:2]) if isinstance(issues, list) else str(issues)[:150]
            lines.append(
                f"  · frame[{f.get('id')}] {f.get('figma_frame_name','?')} "
                f"score={f.get('match_score')} issues: {issues_str}"
            )

    if figma:
        lines.append("### Figma imports")
        for fi in figma:
            lines.append(
                f"- import[{fi.get('import_id')}] {fi.get('file_name','?')} "
                f"status={fi.get('status')} frames={fi.get('frame_count')}"
            )
    return "\n".join(lines)


def generate(db: Session, project_id: int, feature_description: str) -> dict[str, Any]:
    """Generate a PRD for a feature. Saves and returns the artifact id + preview.

    Returns:
        {
            "artifact_id": int,
            "content_md": str,
            "prism_evidence_count": int,
            "loupe_evidence_available": bool,
            "loupe_runs_matched": int,
        }
    """
    project = db.get(Project, project_id)
    if project is None:
        raise ValueError(f"Project {project_id} not found")

    # 1) Context bundle from Prism
    brief = build_brief(db, project_id)

    # 2) Relevant Prism KG entities
    ks = KnowledgeStore(db, agent_type="prd_synthesizer", project_id=project_id)
    entities = ks.find_entities(name_like=feature_description, limit=50)
    # Also pull recent observations per entity
    observations_by_entity: dict[int, list[dict]] = {}
    for e in entities:
        observations_by_entity[e["id"]] = ks.get_observations(e["id"], limit=3)

    # 3) Loupe evidence
    loupe_bundle = loupe_client.fetch_feature_evidence(project_id, feature_description)

    # 4) Build LLM prompt
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    prism_block = _format_prism_evidence(entities, observations_by_entity)
    loupe_block = _format_loupe_evidence(loupe_bundle)

    user_prompt = f"""Produce the PRD for this request.

Project: **{project.name}** (id={project.id})
Project description: {project.description or '(none)'}
Feature: **{feature_description}**
Timestamp: {now}

## Prism evidence (market lens)

{prism_block}

## Loupe evidence (build lens)

{loupe_block}

## Additional project context

{brief.to_prompt_context()[:1200]}

---

Now produce the PRD. Cite every claim by `[id]`. If a section has no evidence,
explicitly say so. Do NOT fabricate verdicts, match scores, or competitor features
that are not in the evidence above."""

    # 5) Synthesize
    markdown = claude_client.ask(
        prompt=user_prompt,
        system=PRD_SYSTEM_PROMPT,
        max_tokens=3000,
        model=claude_client.DEFAULT_MODEL,
    )

    # 6) Persist as KnowledgeArtifact
    cited_ids = [e["id"] for e in entities[:MAX_ENTITIES_PER_TYPE * 4]]
    artifact_id = ks.save_artifact(
        artifact_type=PRD_ARTIFACT_TYPE,
        title=f"PRD: {feature_description}",
        content_md=markdown,
        entity_ids=cited_ids,
    )

    logger.info(
        "[prd] generated artifact=%d project=%d feature=%r prism_entities=%d loupe_runs=%d",
        artifact_id, project_id, feature_description[:60],
        len(entities), len(loupe_bundle.get("matched_runs", [])),
    )
    return {
        "artifact_id": artifact_id,
        "content_md": markdown,
        "prism_evidence_count": len(entities),
        "loupe_evidence_available": loupe_bundle.get("available", False),
        "loupe_runs_matched": len(loupe_bundle.get("matched_runs", [])),
    }
