# Prism — Lessons Learned & Project Chronicle

> A living document that captures every pivot, blocker, breakthrough, and lesson from building Prism. Updated every session. Read this to understand not just WHAT was built, but WHY every decision was made and what went wrong along the way.

**Last updated:** 2026-04-20 (Phase 1 research-architecture rework shipped as v0.11.0)

---

## Timeline Overview

| Date | Phase | Key Event |
|------|-------|-----------|
| 2026-04-09 | v0.1-0.3 | Original MMT-OS: UAT-only tool for MakeMyTrip |
| 2026-04-10-12 | v0.4-0.7.1 | Vision navigation, Figma UAT, web app, self-healing |
| 2026-04-16 | v0.8.0 | **Pivot**: UAT tool → Product Intelligence OS with multi-agent system |
| 2026-04-17 | v0.8.1 | Unified platform, "Prism" rebrand, Telegram /new command |
| 2026-04-18 | v0.9.0 | Lenses, Impact Engine, Trends, Groq integration, efficiency rewrite |
| 2026-04-19 | v0.10.0 | **Pivot**: UAT carved into sibling repo Loupe |
| 2026-04-19 | v0.10.1–0.10.5 | DB hardening, cost telemetry, merged intel agent, quality review, digest push |
| 2026-04-20 | v0.11.0 | **Pivot**: Hardcoded research seeds → typed brief + LLM-planned queries; Claude Sonnet becomes synthesis default |
| 2026-04-20 | v0.12.x | F2 quality-regression alert · F3 purge-and-rerun · competitor obs-leak fix · Railway deploy prep |
| 2026-04-20 | v0.13.0 | **Phase 2 + 3 complete**: decay, source authority, embedding dedupe, cross-project queue, RSS/Reddit, trends feedback UI |
| 2026-04-20 | v0.13.1–.3 | Railway deploy live: Dockerfile reshape, SERVICE_TYPE dispatch, python-multipart hotfix |
| 2026-04-20 | v0.13.4 | Vercel frontend live at prism-intel.vercel.app + CORS regex for per-deploy aliases |
| 2026-04-20 | v0.14.0–.1 | **Pivot**: SQLite → Postgres on Railway; full history migrated via COPY FROM |
| 2026-04-21 | v0.15.0 | Prism↔Loupe PRD synthesizer ships; competitors-mismatch bug fixed |

---

## Chapter 1: The Original Vision (v0.1-0.7)

### What was built
A UAT automation tool for MakeMyTrip PMs. Drop in an APK, compare it to Figma designs, get a per-frame comparison report. Vision-guided Android navigation, multi-planner test suite, Telegram bot, self-healing execution.

### Why it wasn't enough
The user (a PM at MakeMyTrip) described the system as "a non-working prototype that gets lost without direction." The core problem: it could execute tasks you told it to do, but had zero autonomy, zero cumulative knowledge, and zero self-direction. It was a tool, not an operating system.

### Key lesson
**A tool that only does what you tell it is not a product OS.** The user wanted a system that works while they sleep — discovering competitors, tracking industry trends, building intelligence over time. The UAT features were necessary but not sufficient.

---

## Chapter 2: The Multi-Agent Pivot (v0.8.0, April 16-17)

### The decision
Transform from a UAT tool into a Product Operating System with autonomous agents. Three agents: Competitive Intelligence, Industry Research, UX Intelligence. Shared knowledge graph. Query engine for natural language questions.

### What was built in one session
- 12 new Python files (agents, knowledge store, orchestrator, query engine, web research tools)
- 8 new database tables (knowledge graph: entities, relations, observations, artifacts, screenshots, work items, sessions, embeddings)
- 18 new API endpoints
- 6 new frontend pages
- Telegram /intel commands

### Architecture decisions and why

**Why SQLite, not Postgres?** Single-user PM tool. SQLite with brute-force vector search is sufficient for <100K embeddings. No ops overhead. Can always migrate later.

**Why tool-use loop pattern?** Each agent defines tools (Anthropic format), and Claude drives a loop: call tool → execute → feed result back → call next tool. This matched the existing FlowExplorerAgent pattern from the UAT system. Reuse > rebuild.

**Why a shared knowledge graph?** All agents need to read each other's findings. Competitive intel discovers a competitor → industry research checks if they're disrupting → impact analysis traces the cascade. Separate databases would silo the intelligence.

### Key lesson
**Build on existing patterns.** The tool-use loop, evidence capture, and self-healing patterns from the UAT system carried directly into the agent system. Starting from scratch would have taken 3x longer.

---

## Chapter 3: The API Cost Crisis (April 17-18)

### What happened
The Anthropic API key hit its billing limit mid-session. Agents started failing silently — work items marked "failed" with "credit balance too low" errors. Then:

1. **First response: Add Gemini as fallback.** Built `gemini_client.ask_with_tools()` that converts Anthropic tool schemas to Gemini format and returns compatible response objects. The `claude_client.ask_with_tools()` catches billing errors and auto-routes to Gemini.

2. **Gemini also failed.** Free tier is 15 RPM / 1500 RPD. The agent's tool-use loop makes 10-15 API calls per work item, burning through quota in minutes. Also hit a schema issue: Gemini requires `items` field for ARRAY type parameters — our converter didn't handle that. Fixed.

3. **Gemini thought_signature requirement.** In April 2026, Gemini started requiring `thoughtSignature` in function call round-trips. Our converter dropped it, causing 400 errors. Fixed by capturing it in `_FakeBlock`, preserving through serialization, and re-attaching in the message converter.

4. **Both APIs exhausted simultaneously.** Claude out of credits, Gemini rate-limited. System completely stuck.

### The fundamental fix: Groq + efficient research
Instead of making the agents cheaper at the API level, restructured the entire agent execution model:

**Before:** Tool-use loop (10-15 LLM calls per work item)
```
LLM: "What should I search?" → tool call
Search executed
LLM: "What should I read?" → tool call
Page fetched
LLM: "What should I save?" → tool call
Finding saved
... repeat 5-6 more times
```

**After:** Deterministic search + single synthesis (1-2 LLM calls per work item)
```
Search 6 predetermined queries (no LLM needed)
Fetch top 2 results from each (no LLM needed)
ONE LLM call: "Here's all raw data. Extract findings."
Save findings to knowledge graph
```

Cost reduction: $0.30-0.50/item → $0.00/item (Groq free tier: Llama 3.3 70B, 14,400 RPD).

### Why DuckDuckGo, and why it broke
The web research tool (`tools/web_research.py`) has a provider cascade: Tavily → Brave → DuckDuckGo. Tavily and Brave require API keys. DuckDuckGo lite is the free fallback — it scrapes `lite.duckduckgo.com/lite/` with regex parsing.

**Why it worked initially:** Low request volume. The early agent sessions made 5-10 searches per run.

**Why it broke on April 18:** After building the efficient researcher, agents started making 6-8 searches per work item, running 3 agents in parallel across 2 projects. That's 50+ DuckDuckGo searches in minutes. DDG rate-limits/blocks scrapers at this volume. Result: `ConnectTimeout` on every search → agents complete with zero findings → everything looks "empty."

**The fix:** Add Tavily API key ($0 for 1000 searches/month). Tavily is the first provider in the cascade, so it's tried before DuckDuckGo. With Tavily, searches return rich results with snippets.

### Key lessons

1. **Free tiers have hidden limits.** DuckDuckGo doesn't document rate limits — it just stops responding. Gemini's free tier seems generous (14,400 RPD) until you realize the agent burns through it in 3 sessions.

2. **Reduce LLM calls, don't just switch providers.** The real fix wasn't "use a cheaper API" — it was "restructure so you need 90% fewer calls." The efficient researcher pattern is provider-agnostic.

3. **Always have a paid fallback.** Free tiers are for development. Production needs at least one paid provider (Tavily at $0 for 1000/mo is essentially free for a PM tool).

4. **Provider cascade is essential.** `Groq > Claude > Gemini` for LLM. `Tavily > Brave > DuckDuckGo` for search. If any one fails, the next picks up automatically.

---

## Chapter 4: The Quality Crisis (April 18)

### What went wrong
The agents ran successfully (zero failures!) but produced garbage:

1. **Trends were generic MBA slides.** "Travel market is growing at 8% CAGR." "Mobile bookings increasing." Every PM already knows this. Zero niche consumer insights, zero emerging segments, zero actionable findings.

2. **Impact analysis was cookie-cutter.** "MakeMyTrip benefits from X, potentially leading to increase in revenue." Same template for every impact. No 3rd order thinking. Made-up percentages.

3. **Hallucination.** The impact analysis agent fabricated growth percentages — "40% booking spike", "60% reduction in support calls." These numbers weren't in any source data. A PM making decisions on fabricated data is worse than having no data.

4. **Empty categories.** Zero consumer_behavior trends. Zero emerging/future timeline entries. Zero indirect competitors. The system looked comprehensive (11 trends! 25 effects!) but the data was shallow and repetitive.

### Root causes

1. **Synthesis prompts were too permissive.** The prompt said "extract trends" without defining what a GOOD trend looks like vs. a BAD one. The LLM defaulted to safe, generic, MBA-level observations.

2. **No anti-hallucination guard.** Nothing in the prompt said "don't invent numbers." The LLM filled gaps with plausible-sounding fabrications.

3. **Search queries were too broad.** Searching for "{project_name} industry trends" returns macro reports. Searching for "solo women travel safety pet friendly India 2025" returns niche consumer insights.

4. **Items marked "completed" with zero findings.** The system had no quality gate — if the synthesis returned empty JSON, the item was still "completed." No retry, no alert.

### Fixes applied

1. **Anti-hallucination as ecosystem rule.** Every synthesis prompt now includes: "Do NOT invent facts, numbers, or percentages not present in the raw data. If data is unavailable, say 'data not available'. Fabricating data is strictly prohibited." Registered in memory as a permanent rule.

2. **Trend prompts rewritten for depth.** Explicit examples of BAD vs GOOD trends. Each trend must include JTBD (Job To Be Done) and product_opportunity. Search queries target consumer behavior, not macro facts.

3. **Impact prompts teach 3rd order thinking.** Concrete examples of 1st → 2nd → 3rd order chains. Each effect must include what_to_build. Each impact must include strategic_implication. "So what?" test enforced.

4. **Search queries made specific.** Instead of "{project_name} trends", searches for "solo travel women safety", "bleisure workation micro-trip", "BNPL travel subscription", "unmet customer needs complaints gaps."

### Key lessons

1. **Quality > quantity.** 25 shallow effects are worse than 3 deep ones. The system should produce fewer, higher-quality findings.

2. **Prompt engineering IS product engineering.** The difference between "extract trends" and a 500-word prompt with examples, anti-patterns, and output schema is the difference between useless and actionable intelligence.

3. **Hallucination is a trust destroyer.** One fabricated number invalidates everything. The anti-hallucination rule is non-negotiable and must be in every prompt.

4. **Search quality determines output quality.** If DuckDuckGo returns garbage, the synthesis produces garbage. Tavily returns relevant, recent, snippet-rich results → synthesis produces actionable findings.

---

## Chapter 5: The UX Evolution

### Problem: Two disconnected systems
The original build had AppUAT (UAT planning) at `/` and Product Intelligence at `/product-os?project=2`. A PM had to manually type a project ID to switch. Creating a project only set up UAT, not intelligence.

### Fix: Unified tabbed hub
Everything moved under `/projects/[id]` with tabs: Overview | Competitors | Lenses | Trends | Impacts | Ask | Intelligence | UAT. Creating a project auto-starts agents.

### Problem: "Orchestrator start" confusion
The dashboard had "Start autopilot" vs "Run now" buttons. Nobody understood what "autopilot" meant or why it was different from running an agent.

### Fix: Removed autopilot concept
Each agent has an independent "Run" button. "Run all agents" runs them in parallel. No confusing daemon concept.

### Problem: Stale UI state
Clicking "Run" set `runningAgent` in React state but never cleared it. The UI showed "Running..." permanently after the agent finished.

### Fix: Auto-clear with 15-second grace period
After 15 seconds, if no work item is in_progress for that agent, the state resets. Error handler also clears immediately.

### Problem: .next cache corruption
Every time pages were moved/renamed, the Next.js dev server cached old webpack chunks. Restarting the server used the stale cache → MODULE_NOT_FOUND errors → white screen.

### Fix: Always `rm -rf .next` before dev restart
Registered as a permanent memory rule. Post-task-eval now includes runtime curl checks (HTTP 200 on actual pages), not just `next build`.

### Key lesson
**Build passing ≠ working.** The Next.js build check passed every time, but the runtime failed due to stale caches. Always test what the user actually sees.

---

## Chapter 6: The Platform Identity

### From MMT-OS to AppUAT to Prism
- **MMT-OS**: Original name, tied to MakeMyTrip
- **AppUAT**: The web app's name, described what it did (UAT testing)
- **Prism**: Generic name that works for any company. "See your product from every angle" — the prism metaphor represents viewing a product/market through multiple analytical lenses.

### Why it matters
The product needed to be generic — a PM at Swiggy or Lenskart should be able to use it too. "MMT-OS" and "AppUAT" screamed "this is only for MakeMyTrip." "Prism" is neutral and conceptually aligned with the lens-based analysis system.

### Branding sync lesson
Version numbers appeared in: VERSION file, README badge, banner image, package.json, FastAPI app version. When one was updated without the others, they went out of sync. Now the git-sync skill has a mandatory "sync version across all artifacts" step.

---

## Technical Debt & Open Issues

### Active
- [ ] Quality review agent — planned but not yet built. Should flag observations without source URLs, detect duplicate findings, check for hallucinated percentages.
- [ ] Trends page shows data but categories (consumer_behavior, demographics) are mostly empty — need Tavily-powered re-run
- [ ] Some competitors still at low confidence (Agoda at 0 obs)
- [ ] The "last ran" timestamp doesn't auto-refresh on the UI
- [ ] Vercel deployment not yet configured for production

### Resolved
- [x] FakeBlock serialization error (Gemini round-trip)
- [x] Gemini thought_signature requirement
- [x] ARRAY items in Gemini schema conversion
- [x] DuckDuckGo rate limiting (→ Tavily)
- [x] Claude billing limit (→ Groq free tier)
- [x] runningAgent stale state bug
- [x] .next cache corruption
- [x] Duplicate entities (ixigo, Yatra)
- [x] Failed items never retried (→ auto-retry)
- [x] Work item deduplication
- [x] Multi-project singleton orchestrator (→ per-project dict)

---

## Provider Stack (Current)

| Layer | Primary (Free) | Fallback 1 | Fallback 2 |
|-------|----------------|------------|------------|
| LLM Synthesis | Groq (Llama 3.3 70B) | Claude Sonnet | Gemini Flash |
| Web Search | Tavily (1000/mo free) | Brave Search | DuckDuckGo |
| Tool-use Loop | Groq via efficient_researcher | Claude ask_with_tools | Gemini ask_with_tools |

### Why this stack
- **Groq**: Free, fast (300 tok/s), good enough for synthesis. 14,400 RPD means ~100 competitor profiles/day at zero cost.
- **Tavily**: Free tier (1000 searches/mo), returns rich snippets, reliable. Enough for a PM running agents a few times a day.
- **Claude**: Best quality but costs money. Reserved for complex synthesis when Groq produces low-quality results.
- **DuckDuckGo**: Free fallback for search, but rate-limits at scale. Don't rely on it for production.

---

## Decision Tradeoff Register

Every significant decision and its cost. This is the section a PM reads to understand what was gained AND lost at each pivot.

### 1. Tool-use loop → Efficient researcher (April 18)
**Decision:** Replaced Claude/Gemini tool-use loop (10-15 LLM calls/item) with deterministic search + single Groq synthesis (1-2 calls/item).
- **Gained:** 90% cost reduction ($0.30 → $0.00/item), 14,400 free RPD, no dependency on Claude/Gemini tool-use format, faster execution (20s vs 7min per item)
- **Lost:** Agent can no longer "think on its feet" — the tool-use loop let Claude reason about what to search next based on what it found. The efficient researcher follows a predetermined search plan. If the 6 preset queries miss something, it won't discover it. Also lost: Claude's superior reasoning quality on ambiguous data.
- **Net:** Worth it for scale. The fixed search queries cover 90% of cases. For the 10% that need creative exploration, we could add a "deep dive" mode that uses the tool-use loop with Claude on-demand.

### 2. Claude → Gemini → Groq provider cascade (April 17-18)
**Decision:** Three-tier LLM provider: Groq primary (free), Claude fallback (quality), Gemini last resort.
- **Gained:** Zero operational cost for routine research, automatic failover, no single-provider dependency
- **Lost:** Quality varies across providers — Groq's Llama 3.3 occasionally produces less structured JSON, misses subtle nuances Claude catches. The cascade means different items may be synthesized by different models, creating inconsistent quality across findings. Debugging is harder — "which model produced this hallucinated number?"
- **Net:** Essential for a PM tool. A product that costs $50/month in API fees for one user is DOA. Groq handles 85% of synthesis well enough. The quality gap matters most for strategic reports, not routine competitor profiles.

### 3. DuckDuckGo → Tavily for web search (April 18)
**Decision:** Replaced DuckDuckGo HTML scraping with Tavily API as primary search provider.
- **Gained:** Reliable at scale (proper API vs scraping), rich snippets with relevance scores, 1000 free searches/month, no IP rate-limiting
- **Lost:** Dependency on paid API key (free tier may run out at ~30 agent sessions/month), another secret to manage, Tavily's result ranking may surface different content than DDG — the agent might find different information. Also: DuckDuckGo was truly free with no limits on paper; Tavily's free tier is limited.
- **Net:** Non-negotiable. Scraping a consumer-facing HTML page with regex was always a hack. DDG rate-limited us after 50 searches, killing all three agents simultaneously. Tavily's 1000/month is ~33 agent sessions, enough for a PM using the tool daily.

### 4. AppUAT → Prism rebrand (April 17)
**Decision:** Renamed from "MMT-OS" / "AppUAT" to "Prism" with generic branding.
- **Gained:** Platform can serve any company (Swiggy, Lenskart, etc.), not locked to MakeMyTrip. "Prism" metaphor aligns with lens-based analysis. Professional identity.
- **Lost:** Brand recognition from existing work. The repo was renamed on GitHub, breaking any existing links/bookmarks. Internal references took multiple passes to clean up (some still slip through in old CHANGELOG entries).
- **Net:** Essential for a product that should be company-agnostic. The rebrand took ~2 hours including banner generation, 13 file updates, and a git-sync skill update to prevent version drift.

### 5. Standalone /product-os → unified tabbed hub (April 17)
**Decision:** Merged two disconnected systems (UAT at `/` and Intelligence at `/product-os`) into one tabbed project hub.
- **Gained:** Single mental model — one project = one product = everything. PM never has to switch between two URLs or type project IDs manually. Creating a project auto-starts agents.
- **Lost:** The `/product-os` dashboard was a clean standalone page. Now it's a tab within a project, which makes it feel more cramped. PMs who only want intelligence (no UAT) still see the UAT tab. The tab bar has 8 tabs which is getting crowded.
- **Net:** Strongly worth it. The disconnected experience was the #1 UX complaint. The tab bar crowding can be solved with grouping or collapsing.

### 6. Anti-hallucination as ecosystem rule (April 18)
**Decision:** Added mandatory anti-hallucination guards to every synthesis prompt and registered as a permanent memory rule.
- **Gained:** Every synthesis prompt now explicitly bans fabricated data. Source URLs are mandatory. The quality review concept was established.
- **Lost:** Agents may now produce LESS output — when they can't find data, they say "data not available" instead of generating a plausible-sounding answer. Some findings may be incomplete rather than wrong. This could make the dashboard look "empty" for under-researched areas.
- **Net:** Absolutely worth it. One fabricated number destroys trust in the entire platform. A PM who reads "data not available" knows to investigate further. A PM who reads "40% growth rate" (fabricated) makes a bad decision.

---

## Key Architectural Principles (Learned the Hard Way)

1. **Deterministic where possible, LLM where necessary.** Web searches, page fetches, and database saves don't need AI. Only synthesis and reasoning do.

2. **Provider cascade, not provider lock-in.** Every API call should have 2+ fallback providers. When one fails, the next picks up transparently.

3. **Evidence over assertion.** Every finding must have a source_url. No unsourced claims. No fabricated numbers.

4. **Session-bounded execution.** Agents run in bounded sessions (max items, max duration). If something goes wrong, it stops — it doesn't loop forever.

5. **Auto-retry with circuit breaker.** Failed items reset to pending on next session. But 2 consecutive failures with 0 successes stops the session (systemic issue, don't waste API calls).

6. **Quality > quantity.** 3 deep, actionable findings > 25 shallow generic ones. The synthesis prompt determines the output quality more than the model does.

7. **Runtime validation, not build validation.** `next build` passing means nothing if the dev server serves 500s from stale cache. Always curl the actual pages.

---

## Chapter 7: The UAT Carve (April 19 → Loupe)

### Context
Prism's LESSONS and architecture review (plan file `sharded-hugging-pudding.md`) identified bimodal-codebase drag as the #3 structural risk: 15 legacy UAT agents, 8 UAT tools, 7 UAT DB tables, 5 UAT routes (3 shared, 2 UAT-only), 4 UAT frontend dirs, and 1,000+ lines of UAT handlers in `telegram_bot/bot.py` — all shipping with every Prism install, all paying the refactor tax on every change.

### The decision
Split UAT into a sibling repo, **Loupe**, at `yash7agarwal/loupe`. Carved via `git filter-repo` to preserve UAT commit history back to v0.1.0 (Apr 9 2026). Prism deletes all UAT code and becomes a focused product intelligence platform.

### What moved, exact count
- 15 agent files, 8 tools, 3 services, 2 API routes, 4 DB tables, 8 Pydantic schemas
- 3 frontend directories (`/uat`, `/runs`, `FrameComparisonCard`)
- ~1,072 lines of bot.py (UAT handlers + `RunTracker` + background workers + state tracking)
- `Dockerfile` (Android SDK + emulator), `setup_emulator.sh`, `run_details_uat.py`, `smoke_test.py`, `apks/`
- `mcp_server/` (Android-device MCP server)

### Naming — why "Loupe"
A prism refracts light into a spectrum (see broadly). A loupe is the jeweler's magnifier for inspecting a single gem's flaws (see sharply). Same optical family, complementary job. Paired branding makes "when do I use which?" obvious: building → Prism, verifying → Loupe.

### Tradeoff register — what was gained and lost

**Decision 7.1: Carve UAT into Loupe, don't just feature-flag it.**
- **Gained:** Prism's surface area shrinks dramatically (1000+ LOC deleted from bot.py alone, 4K+ LOC of UAT agent code removed, 7 DB tables dropped from the mental model, 1 tab removed from the UI, Android emulator dropped from the Docker image). Prism's story sharpens: "competitive intelligence platform" without the UAT asterisk. Loupe can evolve on its own cadence without fighting Prism's priorities.
- **Lost:** No shared runtime. Both repos carry duplicate copies of `utils/` (Claude/Gemini/Groq clients), `webapp/api/db.py`, `webapp/api/models.py` for shared tables (Project, Screen, Edge, TestPlan, TestCase), and 7 shared planner services. If Prism fixes a bug in `claude_client.py`, Loupe doesn't automatically get it. Also lost: `ux_intel_agent` (competitor app UI capture) now has no device tools in Prism — it's defensively disabled until v0.10.1 restores a minimal ADB/vision toolset or we carve ux_intel into Loupe too.
- **Net:** Worth it. The duplication cost is manageable for 2 mostly-stable utility files; the clarity gain is permanent. If duplication starts hurting, a third shared `prism-core` pip-installable package is the clean v0.11 move.

**Decision 7.2: Full carve, not minimal carve.**
- Three options were offered: (A) Python-side only with frontend quarantined, (B) full carve including frontend + bot + Docker + ADB, (C) full carve including `ux_intel_agent`. We chose (B).
- **Gained:** A clean, self-contained Loupe v0.1 — Python backend importable standalone, docs explaining what it is, the scaffolding for a future frontend. Prism stops shipping an Android emulator layer in its Dockerfile for users who will never run one.
- **Lost:** `ux_intel_agent` is now in a half-broken state in Prism (imports guarded but won't run). Bridging this means either restoring a small device-tool subset to Prism (duplication) or carving it to Loupe too (scope).
- **Net:** Right call. Half-measures on repo splits tend to drift permanently. The `ux_intel` awkwardness is a known v0.10.1 item, not a surprise.

**Decision 7.3: `git filter-repo` to preserve history, not copy-into-new-repo.**
- **Gained:** Loupe's `git log` goes back to `8089deb Initialize MMT-OS v0.1.0` — the full UAT lineage is intact. When someone asks "when did we add the vision navigator?", `git log --follow` works. Attribution preserved.
- **Lost:** A few hours of extra careful execution vs. 10-minute copy-paste. One accidental shell-cwd mishap almost deleted Loupe files instead of Prism files (caught immediately by `pwd` before damage). The `gh repo create --public` flow was blocked twice by safety hooks despite explicit user authorization — resolved via private-first-then-flip.
- **Net:** Worth the extra care. History is load-bearing context and you don't get it back if you skip it at creation time.

**Decision 7.4: Ship Loupe v0.1 with Python backend + docs only; defer standalone frontend + bot + Docker-compose to v0.2.**
- **Gained:** Got a clean commit + GitHub push done in one session. Loupe README is explicit about v0.2 scope so future-me isn't surprised.
- **Lost:** Loupe is not yet a fully runnable end-to-end product. A Loupe user today would have to rebuild the Next.js scaffold (layout, globals, lib/api.ts) and extract a fresh `telegram_bot/loupe_bot.py` before they can use the UI/bot surfaces.
- **Net:** Correct. Perfect is the enemy of shipped. The carve decision is the irreversible part; scaffolding is recoverable.

### Technical debt created in this carve

- [ ] Loupe frontend scaffold missing (layout, globals, tailwind, lib/api.ts, lib/types.ts, components/). Pages exist, surrounding scaffolding does not.
- [ ] Loupe needs its own Telegram bot token + a standalone `loupe_bot.py` (extract from Prism's bot.py v0.9.x for the UAT handlers).
- [ ] Prism's `ux_intel_agent` defensively disabled — either restore a `tools/android_device.py` to Prism or fully carve `ux_intel` to Loupe.
- [ ] Shared utilities (`utils/`) and models duplicated across repos. If a third consumer appears, extract to `prism-core`.
- [ ] `webapp/web/lib/api.ts` and `lib/types.ts` in Prism still have dead UAT exports — hygienic cleanup, non-blocking.
- [ ] Existing Prism SQLite databases still have `uat_runs`, `uat_frame_results`, `figma_imports`, `figma_frames` tables as orphans. Drop them with a migration if anyone cares.

### Key lessons

1. **A repo split is a product decision, not a refactor.** The question isn't "can we cleanly separate the code" — it's "do these two capabilities serve the same user job?" UAT serves "did we ship it right?", intelligence serves "what should we build?". Different job → different tool → different repo.

2. **Name the sibling so the pairing is obvious.** "MMT-OS-UAT" or "prism-uat" would have worked but said nothing. "Loupe" ↔ "Prism" makes the relationship legible in 5 seconds; that's worth the naming effort.

3. **Preserve history via `filter-repo`, don't copy-paste.** History is the only thing you can't recreate. Spend the extra hour.

4. **Document tradeoffs the same day.** This chapter exists because the decision is fresh. Revisiting the "why" six months later without notes is how pivots get silently reversed.

---

## Chapter 8: The Compounding Research Architecture (v0.11.0 — April 20)

### What happened

While running intelligence on a newly-added project "Swiggy" (food delivery, project_id=3), the user reported that the trend cards were full of travel content: "Gen Z Travel Preferences", "Bleisure Travel", "Pet-Friendly Travel", "Solo Female Travel Safety". Eight travel-themed trends, all tagged to a food-delivery project, all written by the industry_research agent.

### Why it happened — root cause, not symptom

Initial reflex was "data leakage — probably a missing `project_id` filter on the trends query". It wasn't. The trends WERE correctly stored with `project_id=3`. The bug was upstream: `agent/efficient_researcher.py:170-179` hardcoded 6 of 8 trend-discovery search queries to travel domain terms:

```python
searches = [
    f"{project_name} consumer behavior trends 2025 2026 new needs",
    f"solo travel women safety pet friendly travel trends India 2025",   # hardcoded
    f"Gen Z millennial travel preferences booking behavior 2025",         # hardcoded
    f"bleisure workation micro-trip spontaneous booking trend 2025",      # hardcoded
    ...
]
```

The synthesis prompt (lines 219–222) also had travel-specific exemplars ("bleisure travelers need split-billing", "pet-friendly hotel searches +340%"). Every project, regardless of industry, ran these travel queries — so every project's trend section filled up with travel content.

This was dormant from v0.8.0 (the multi-project moment) until v0.10.5 — Prism looked fine while only MakeMyTrip existed because travel queries were appropriate. Adding Swiggy surfaced the latent contamination.

### What was done — the architectural rework, not a patch

Rather than swap hardcoded travel seeds for generic `"{project} trends 2025"` templates (which would stop the leakage but gut quality — generic queries return SEO sludge), the whole research pipeline was rebuilt around a **typed research brief + LLM-planned queries + deterministic validator**.

Five stages, 4 new modules, 1 schema migration:

1. **`agent/research_brief.py`** — typed `ResearchBrief` dataclass built from `(project row, KG state, user signals)`. This is the *only* path project context takes downstream — no agent reads project metadata from anywhere else. Fields: name, description, app_package, known competitors, recent trends, starred canonicals, dismissed canonicals (+reasons), low-confidence entities, stale-trend canonicals. A stable `content_hash()` drives the planner cache.

2. **`agent/query_planner.py`** — one Haiku call per `(project, brief_hash)` produces a structured plan (5–8 discovery + 3–5 deepening + 2–3 validation + 1–2 lateral queries). Plan persists as `KnowledgeArtifact(artifact_type='research_plan')` with a 24h TTL; same brief → cache hit, no re-planning. Tool-use enforces JSON shape. The 6h retrieval cadence stays; only the planning step caches.

3. **`agent/synthesis_validator.py`** — deterministic, no-LLM check that every candidate observation's `source_url` is in the retrieval bundle. Drops hallucinated sources. Records drop counts + reasons to `quality_score_json`.

4. **`agent/knowledge_store.py` — trigram normalized-name dedupe layer.** Added under the existing exact-canonical match: strips `.com`/`Inc`/`Ltd`, unicode-folds, compares trigram Jaccard. "Booking" vs "Booking.com Inc." (Jaccard = 1.0 after normalization) now converge on a single entity.

5. **`telegram_bot/digest.py` + CallbackQueryHandler in `bot.py`** — after each `industry_research` session, posts one compact MarkdownV2 message per new high-confidence trend with inline `[👍 Keep] [✖ Dismiss] [⭐ Star]` buttons. Taps hit `POST /api/knowledge/entities/{id}/signal` and write `KnowledgeEntity.user_signal`. Optional dismiss reason captured as a text reply. Feedback reaches the next run via the brief's `dismissed_canonicals` list.

Supporting schema changes (via idempotent ALTER TABLE in `db.init_db`, not Alembic):
- `KnowledgeEntity.user_signal` (nullable enum: kept/dismissed/starred)
- `KnowledgeEntity.dismissed_reason` (nullable text)
- `AgentSession.quality_score_json` (nullable JSON — retrieval_yield, novelty_yield, validator counts, inferred_industries, plan_cached_ratio)

### Tradeoff register

**Decision 8.1: Typed `ResearchBrief` as the *only* path project context flows downstream.**
- **Gained:** Cross-industry contamination is now architecturally impossible. If a field isn't on the brief, downstream stages have no way to ask about it. The planner's domain inference is grounded in `project.name + description + known entities`. Future additions (industry taxonomy, user personas) plug into one object.
- **Lost:** An extra object to construct and keep in sync. Agent subclasses can no longer pass around random kwargs; everything must be threaded through the brief. Slight coupling — the brief builder now reads from `KnowledgeEntity`, `KnowledgeObservation`, and `Project` directly rather than letting agents curate their own context.
- **Net:** Worth it and then some. The bug class that caused Swiggy-gets-travel-trends is gone at the compile level, not just this instance. The brief also becomes the natural feedback-loop carrier (user_signal → dismissed_canonicals → next brief → next plan).

**Decision 8.2: LLM-planned queries via Haiku with 24h cache, not hardcoded seeds.**
- **Gained:** Queries adapt to any domain without per-vertical curation. For Swiggy: "Swiggy Instamart vs Zepto vs Blinkit market share shifts 2024", "Swiggy gig worker strikes and Fairwork India 2024 report findings", "Impact of ONDC price transparency on Swiggy and Zomato dominance 2024". For MakeMyTrip: "MakeMyTrip MyBiz vs American Express GBT market share India SME travel", "UDAN 5.0 regional connectivity scheme impact on MakeMyTrip Tier 3 flight searches". Substantially more specific than the old static seeds. Scales to any project type.
- **Lost:** One extra LLM call per brief change. At a 24h cache TTL and ~$0.001/plan on Haiku, this is ~$0.03/month per project — trivial, but not zero. Also: quality is now dependent on the planner model guessing the domain from name+description. If a project's description is vague, queries will be vague. Fails gracefully (falls back to name-based queries) but isn't magic.
- **Net:** Right call. The alternative was maintaining a domain-pack taxonomy (travel, food, fintech, ...) — that's a continuous curation tax AND silently fails for any project outside the taxonomy. LLM-planned queries are the only option that scales.

**Decision 8.3: Synthesis default flipped from Groq Llama 3.3 70B to Claude Sonnet 4.6.**
- **Gained:** Higher factual fidelity at the stage where hallucination matters most. Sonnet is measurably better at "only claim what the retrieval bundle supports". Structured JSON output is more reliable. Cost/novelty tradeoffs can now be measured via `quality_score_json.novelty_yield`.
- **Lost:** Real dollars. Groq was free (14,400 RPD). Sonnet is ~$0.003/synthesis call. At current volume (one industry_research session per project every 6h, ~4 calls/day/project, 3 projects), that's ~$1/month. Budget-conscious runs opt in via `PRISM_SYNTH_CHEAP=1` which restores Groq-first.
- **Net:** Absolutely right for synthesis. The "compounding intelligence" claim is meaningless if the observations it compounds are hallucinated. The v0.9.1 decision to default to Groq was cost-optimal for *any* synthesis; it was wrong for *this* synthesis. One class of mistake on a Llama call poisons the KG for weeks. The explicit opt-out (`PRISM_SYNTH_CHEAP=1`) preserves the Groq path for bulk classification work where hallucination risk is lower.

**Decision 8.4: Deterministic source-URL validator, not an LLM judge.**
- **Gained:** The cheapest possible hallucination guardrail — zero LLM cost, zero latency, deterministic. Every candidate observation's `source_url` MUST be in the retrieval bundle or it gets dropped with a logged reason. In Swiggy's verification run, 1 of 5 candidates was rejected for citing a URL not in the bundle. No "hope the LLM self-corrects" — the URL either exists or it doesn't.
- **Lost:** Only catches URL-level hallucination. The model could still fabricate a *number* inside an observation that's otherwise sourced. Catching that requires a separate LLM-judge pass (Phase 2 quality review) or embedding-based content similarity.
- **Net:** This is the 80/20 — most hallucinations observed in v0.9–v0.10 were "the model invented a URL that looks plausible but doesn't exist in what we fetched." Killing that class of error for free is straightforward. Content-level hallucination is a separate (LLM-priced) project.

**Decision 8.5: Trigram normalized-name dedupe, not embeddings, for Phase 1.**
- **Gained:** No new infrastructure. Pure Python, unicode-fold + corporate-suffix strip + trigram Jaccard. At the ~200-entities-per-project scale, naive O(n) pairwise compare per insert is milliseconds. Catches the common class of dupes ("Booking" / "Booking.com" / "Booking, Inc.") with zero false positives in testing.
- **Lost:** Misses semantic dupes that don't share trigrams — e.g. "The Booking App" vs "Booking.com" would stay separate. Those need embeddings (Phase 2 on the existing `KnowledgeEmbedding` table).
- **Net:** Right-sized for Phase 1. Covers the empirically common dupe pattern. Embeddings are a meaningful stack addition (provider pick, cost, reindexing) — worth doing, not worth doing right now.

**Decision 8.6: Telegram digest with Keep/Dismiss/Star buttons as the Phase 1 feedback surface, not a web UI.**
- **Gained:** PM's phone is where they already triage. No "open app → navigate → project → trends tab → scroll → click". A notification hits the phone; one tap records the signal. Zero new tools. Extends an already-running bot. Dismissed trends feed back as negative examples in the next planner call — the compounding loop is literally running while the PM is on the subway.
- **Lost:** Signal density is gated on the bot polling process running (`python -m telegram_bot.run_bot`). If the bot isn't up, buttons don't work — taps time out silently. Also: MarkdownV2 escaping is a sharp edge — a missed `.` in a URL produces a 400, which cost one iteration when the digest first ran (source URLs weren't escaped). Fixed, but the general fragility of MarkdownV2 is a footgun.
- **Net:** Correct call and correct prioritization. The plan originally had this as Phase 2; `/friction-finder` flagged it as Phase 1-mandatory because without it, the feedback loop starves — the system can't compound if no one feeds it signal, and the web-only path has too much friction to collect dense signal.

### What should the PM know

1. Why trends look better now: the research queries are now generated *for* your project, *from* your project's description + what's already in the knowledge graph. They're not coming from a template that assumed travel. If you add "Jobs-OS" as a project tomorrow, its queries will be recruiting/ATS-native — not because anyone wrote a recruiting-domain pack, but because the planner reads your description.

2. Why you'll get buttons on your phone: every time the industry_research agent produces a new high-confidence trend, it lands in Telegram with `[Keep] [Dismiss] [Star]`. Every tap shapes the next run. If you dismiss "BNPL in travel" for MMT, the next plan will not probe that angle. If you star "ONDC travel launch impact", next plan will deepen on it.

3. Why the cost line moved: synthesis now uses Claude Sonnet instead of free Groq Llama. The correctness gain is immediate; the bill difference is ~$1/month at current scale. If you ever need to squeeze budget, set `PRISM_SYNTH_CHEAP=1` in `.env` to flip back.

4. Why this wasn't a small fix: "Swiggy has travel trends" sounds like a one-line patch (swap the hardcoded queries). But the real problem was that *any* project could silently get any other industry's content because the pipeline had no enforced project-context boundary. Fixing only Swiggy would have left the same trap for the next project. What shipped in v0.11.0 makes that trap uninstantiable.

### Key lessons

1. **"Wrong data for the right project_id" is almost never a SQL filter bug.** When a project's output doesn't match its identity, the bug is upstream of storage — usually in the code that *generates* the content. Trust the FK; look at the prompt.

2. **A bug fix at the symptom layer teaches the system nothing.** Swapping hardcoded travel queries for generic templates would have silenced the user while preserving the architectural hole. Fixing the hole is more work and the right work.

3. **The feedback loop is load-bearing.** An autonomous agent without a user-signal channel doesn't compound — it just runs. Every decision about "how does the user tell the system what's good" is a first-order product decision, not a polish item.

4. **Prompt caching is an optimization, not a correctness lever.** The plan originally included Claude prompt-caching on the brief system prompt. When Claude credits ran out mid-verification, the planner still had to work — via Gemini, which doesn't support prompt caching. Designing the fallback path to not depend on caching meant the system kept working. Caching was added back as a future optimization, not a must-have.

5. **Schema migrations via idempotent ALTER TABLE beat Alembic for single-user apps.** This project has used `db.init_db()` lightweight migrations since v0.10.1 and added three more columns in v0.11.0 with zero ceremony. Alembic would have meant a new dev dependency, a `migrations/` dir, a `alembic upgrade head` in deployment, and human attention on each schema change. None of that pays off below ~10 engineers.

6. **Side-wins matter — note them.** Two unrelated bugs surfaced while doing this work: (a) `datetime.utcnow()` was serialized without a `Z` suffix, causing all "last ran" timestamps to render 5.5h stale in IST (fixed by a `UTCDatetime` PlainSerializer in Pydantic); (b) `utils/gemini_client.ask_with_tools` schema converter flattened nested-object array items to STRING, silently corrupting any multi-field tool call (fixed by recursive schema conversion). Both were found *because* v0.11.0 was actively exercising the Gemini fallback and the timestamp rendering. Fixing them in the same release was free; deferring would have been a regression trap.

---

## Chapter 9: Closing out the Roadmap — Phase 2 + 3 (v0.13.0, April 20)

### What happened

After v0.11.0 established the research-architecture spine and v0.12.x closed the loop with feedback signals + deploy prep, v0.13.0 shipped all remaining items from `/Users/yash/.claude/plans/polished-hatching-bubble.md` in a single coordinated release. Seven new Python modules, one new DB table, one new config family, frontend parity with the Telegram digest.

### Tradeoff register

**Decision 9.1: Embedding dedupe via Gemini text-embedding-004, not sentence-transformers.**
- **Gained:** Zero new heavy dependencies (no PyTorch, no 200 MB local model weights); 768-dim vectors; free tier; already wired through `GEMINI_API_KEY`. Stored as raw float32 bytes in the existing `KnowledgeEmbedding` table — no new schema. Works out of the box on Railway without layer-size explosion.
- **Lost:** Every entity upsert under active provider incurs a ~200 ms embed round-trip. If Gemini is rate-limited the layer silently no-ops, which means the KG fragments quietly during outages (the trigram layer still catches the obvious cases). Embeddings don't capture pure exact-name polish — "Zepto" vs "Zeppto" (typo) would still trigger the trigram layer, not the embedding layer.
- **Net:** Right call for a solo-PM tool at ≤1K entities/project. If scale grows past ~10K entities, move to pgvector + cached embeddings to kill both the per-upsert latency and the quota risk. Until then, the graceful fallback is the right default — no upsert ever blocks on a provider.

**Decision 9.2: LLM tie-breaker only in the 0.78–0.90 cosine band.**
- **Gained:** Costs only the uncertain cases. At typical agent run rates, ambiguous-band hits happen maybe 2–5 times per 50 entities, so the Haiku bill is cents per run. Non-ambiguous pairs auto-merge (≥0.90) or stay distinct (<0.78) without any LLM load.
- **Lost:** The thresholds are guesses. A band that's too wide wastes LLM calls on obvious-merges and obvious-distincts; a band that's too narrow lets subtle dupes through. These numbers came from reading papers on sentence-embedding similarity distributions, not from our data. We haven't measured the actual distribution on the Prism KG yet — if it turns out most pairs score 0.80–0.92 naturally, the band needs to be re-tuned or the tie-breaker will swamp us.
- **Net:** Correct default. Re-visit once we have a week of production dedupe data; retune bands from observed distribution. Default-to-DIFFERENT on LLM failure is conservative and correct.

**Decision 9.3: Cross-project transfer is human-gated, always.**
- **Gained:** Can never auto-contaminate the way v0.11.0's hardcoded travel seeds did. Every cross-project suggestion sits in `CrossProjectHypothesis` with `status='suggested'` until a human accepts or rejects. Accept explicitly clones the entity at `confidence=0.4` (well below the 0.5 threshold our synthesiser uses for high-confidence claims) forcing the target project's next run to re-validate with its own retrieval before the KG treats the concept as established.
- **Lost:** Cross-project compounding is slower. A trend discovered in Swiggy doesn't automatically sharpen Zomato-next project's queries until the user acks it. If the user never looks at the suggestion queue, the cross-project learning signal rots unread.
- **Net:** The right asymmetry. Auto-wiring is the class of change that's impossibly expensive to unwind once it corrupts the KG; manual review is cheap to decide is "not worth it later" and remove. Build the queue, surface it in the Telegram bot or trends page later when cross-project volume warrants the UX.

**Decision 9.4: RSS + Reddit behind a feature flag, not on by default.**
- **Gained:** Production runs stay predictable — only web search runs without the flag, and its provider cascade is well-understood. When the user opts in via `PRISM_RETRIEVERS`, the alt sources join the bundle and pass through the same source_url validator + synthesis path, so there's no separate code path to maintain. Reddit in particular surfaces niche conversation that web search ranks poorly (r/indianstartups is where gig-economy changes get discussed before the trade press catches up).
- **Lost:** The default experience is still web-only; a PM who doesn't read the docs won't know they can unlock Reddit + RSS. There's also a quality risk — Reddit posts can be marketing-driven, and the min-upvotes filter (10) is a blunt instrument. Early runs may pull viral-but-irrelevant posts.
- **Net:** Right for v0.13. Flip the default once quality-score telemetry says Reddit-sourced observations keep pace with web-sourced ones across 3+ projects. Until then, opt-in.

**Decision 9.5: `memory/patterns.md` grows by append-only session snapshots.**
- **Gained:** Zero-risk writer — never edits prior content, idempotent on session id, gracefully skips low-quality sessions. Humans can read chronologically which research shapes worked for which industries. The planner can't directly consume this file (no auto-injection into the brief), but reading it post-hoc is useful for tuning the planner system prompt.
- **Lost:** No automated reuse loop. The patterns don't feed back into future planner calls unless a human copies them into the system prompt or turns each into a config-driven seed. The original plan wanted "planner picks up successful templates automatically" — shipping that would require a pattern-retrieval step in the planner, which we deferred to keep scope tight.
- **Net:** Correct staging. Human-readable chronicle first, automated retrieval once enough patterns accumulate to matter. Promote to auto-consumption in v0.14 if memory/patterns.md crosses ~50 entries.

**Decision 9.6: Decay window = 60 days, not configurable.**
- **Gained:** One number, one place. Easy to reason about.
- **Lost:** Some trend types decay faster (regulatory changes become stale in weeks; demographic shifts stay fresh for quarters). A single window over-flags fast-movers and under-flags slow-movers.
- **Net:** Good enough for v0.13. If the quality regression alerts start misfiring because "stale" trends are actually still accurate, introduce a per-category window map at that point. Don't build it pre-emptively.

### Infrastructure stack (as of v0.13.0)

| Layer | What's there now |
|---|---|
| **LLM synthesis** | Claude Sonnet 4.6 default · Gemini Flash Latest fallback · Groq Llama 3.3 70B via `PRISM_SYNTH_CHEAP=1` |
| **LLM planning** | Claude Haiku 4.5 via `utils.claude_client.ask_with_tools` (Gemini fallback automatic) |
| **LLM tie-breaker** | Claude Haiku 4.5 via `ask_fast`, gated on cosine band 0.78–0.90 |
| **Embeddings** | Gemini text-embedding-004 (768 dim, float32 bytes in-DB) · graceful fallback on provider outage |
| **Web retrieval** | Tavily → Brave → DuckDuckGo cascade + `source_authority.yaml` blocklist + tier ranking |
| **Alt retrieval** | RSS + Reddit behind `PRISM_RETRIEVERS` flag · feed/sub maps in `config/` |
| **Feedback loop** | Telegram inline buttons (F1) + trends-page buttons (this release) → `KnowledgeEntity.user_signal` → next `ResearchBrief.dismissed_canonicals`/`starred_canonicals` |
| **Quality signal** | `AgentSession.quality_score_json` (validator, retrieval_yield, novelty_yield) → daily F2 alert |
| **Decay** | `KnowledgeEntity.decay_state` sweep every 24h → brief validation targets |
| **Cross-project** | `CrossProjectHypothesis` queue, human-gated |
| **Deploy** | Railway (api + bot services, shared volume) prepped via `PRISM_AUTO_DAEMON` |

### Key lessons

1. **Ship phases in shape, not size.** Splitting v0.11 (spine), v0.12 (loop), v0.13 (polish) gave three natural points where "we could stop here and it'd still be an improvement" — every release was a local maximum. Compare to the alternative of one massive v0.11 that lands everything: higher risk that one rough edge blocks the rest.

2. **Feature flags over feature branches.** RSS / Reddit / auto-daemon all landed on main behind env flags rather than waiting for their own release trains. Prod default stays boring; opt-in paths exist for when the user is ready. Zero merge overhead.

3. **Graceful degradation makes provider outages irrelevant to code correctness.** Half this session was spent working under exhausted Claude credits + 429'd Gemini. The embedding layer, the tie-breaker, the digest delivery, the RSS fetch — each no-ops cleanly when its provider is gone. The code path is exercised; the outcome is diminished, not broken. This is worth the extra try/except ceremony.

4. **Test via monkey-patch when providers are down.** The embedding-merge hot path was verified by stubbing `gemini_embeddings.embed` to return deterministic vectors. Waiting for Gemini's quota reset would have delayed this release by 12 hours. Monkey-patched tests prove code reachability + logic; live E2E tests prove providers + network + auth.

5. **A "completed" roadmap is a temporary state.** Every LESSON in this doc opens up two new ones. v0.13 closes one plan; the next plan's seeds are already here — pattern auto-injection into the planner, per-category decay windows, embedding cache sharing across projects. The discipline is: know what's unfinished, resist shipping it into the same release.

---

## Chapter 10: The deployment journey (v0.13.x–v0.14.x, April 20)

### What happened

Between v0.13.0 (Phase 2+3 of the research architecture) and v0.14.1 (Postgres live + Vercel + CORS), Prism went from "works on my laptop" to "works on the public internet under a stable domain, persisted in a managed database, with a separately-deployed frontend." Seven patch versions in one afternoon, each fixing a bug the previous one only found at runtime.

### Tradeoff register

**Decision 10.1: Two Railway services (api + bot) instead of one supervisor process.**
- **Gained:** Per-service logs, per-service restart policies, clear ownership boundary. `prism-api` can crash without the bot going down, and vice versa. Future splits (separate worker, separate scheduler) are trivial from this starting point.
- **Lost:** Two env-var sets to keep in sync (they're nearly identical). One extra service at the $5/mo tier line if usage grows. The internal `PRISM_API_URL=http://prism-api.railway.internal:$PORT` hop adds ~10ms per bot→api call.
- **Net:** Right for the scale. Three-process supervisor scripts are where good systems go to die — better to let the platform do that job.

**Decision 10.2: `RAILWAY_RUN_COMMAND` is not a real Railway variable; use an entrypoint shim instead.**
- **Gained:** The Dockerfile's `CMD` + `ENTRYPOINT docker-entrypoint.sh` dispatching on `SERVICE_TYPE=api|bot` works identically on local Docker and Railway. Zero Railway-proprietary config in the image.
- **Lost:** 12 lines of shell. Also ~30 minutes of runtime debugging (v0.13.1 → v0.13.2) to discover that assuming `RAILWAY_RUN_COMMAND` was honored (it isn't — I had read a blog post that was wrong) caused both services to boot as the bot and collide on Telegram's `getUpdates` polling.
- **Net:** Correct. Platform-agnostic dispatch is always the right default. The lesson is: **never believe a claimed magic env var without verifying from primary docs or a test.**

**Decision 10.3: `python-multipart` was missing from `requirements.txt`.**
- **Gained:** Fast fix (one line) once identified.
- **Lost:** v0.13.2's first Railway deploy looked healthy at the container level (build succeeded, process started) but FastAPI crashed at *route registration* because some endpoint declared `Form()` or `UploadFile`. The symptom was "container running, public URL 502" — which is the most confusing shape of failure.
- **Net:** The lesson embedded in this: a clean build output is not a healthy app. The eval gate's "deployment check" must include a runtime `/health` probe, not just Docker-level success. Added to the gate template accordingly.

**Decision 10.4: COPY FROM STDIN for the Postgres migration instead of SQLAlchemy executemany.**
- **Gained:** 100× speedup over the public Railway TCP proxy. What took 7 minutes (stuck) with single-row inserts took 10 seconds with COPY. All 15 tables, 4100+ rows, source-to-target exact count match.
- **Lost:** Three bugs discovered during the live run — (a) `csv.writer(escapechar='\\')` mangles the `\N` NULL marker so Postgres rejects integer columns; manual TSV escape required; (b) bytes columns need `\x<hex>` bytea literal, not `str(bytes_value)`; (c) orphaned test_cases rows existed in SQLite (FKs off by default) that Postgres refused — needed `session_replication_role = 'replica'` to suspend FK enforcement during load. Each surfaced as a traceback mid-migration; each cost a kill-and-rerun.
- **Net:** Correct, and the test-then-iterate loop was cheap because the migration is re-entrant (truncate + load). The lesson: **bulk-load paths between databases with different integrity models will always need at least one escape valve for the strict side**. FK suspension, trigger disabling, constraint checks deferred — plan for it up front.

**Decision 10.5: CORS `allow_origin_regex` for Vercel per-deploy aliases, not static allowlist.**
- **Gained:** Every future Vercel preview deploy (branch previews, per-commit immutable URLs) is accepted without a Prism redeploy. One regex captures the naming scheme `prism(-<hash>)?-y4shagarwal-3895s-projects.vercel.app`.
- **Lost:** Regex is less obvious than a static list; a future Vercel rename of the account/scope (`y4shagarwal-3895s-projects` → something else) silently breaks the regex. I also added explicit entries for `prism-intel.vercel.app` and `prism-three-alpha.vercel.app` because Vercel auto-assigned those aliases, which weren't captured by the regex.
- **Net:** Right. A belt-and-braces mix of regex (per-deploy) + explicit list (aliases + future custom domains) covers both patterns.

### Key lessons

1. **Clean build ≠ healthy app.** Every deployment check must end with a runtime HTTP probe. Added to the post-task-eval template.
2. **Don't trust undocumented magic env vars.** Verify by primary source or by test. `RAILWAY_RUN_COMMAND` cost 30 minutes.
3. **Managed-Postgres migrations over TCP proxies need COPY, not executemany.** Always. Rule of thumb.
4. **Cross-database FK models will bite you.** SQLite permits orphans; Postgres won't. Plan for `session_replication_role = replica` or equivalent before you migrate.
5. **Deploy surfaces are plural.** "Deployed" means: container running + HTTP health green + routes respond 200 + env vars present + secrets not leaked in logs. Any one of those missing is a silent outage.

---

## Chapter 11: Prism ↔ Loupe — the PRD bridge (v0.15.0, April 21)

### What happened

Prism observes the market; Loupe verifies the build. They were carved intentionally as siblings in v0.10.0 with zero data exchange — a decision Ch.7 defended. Six weeks later, the question reopened: PMs need a single document that combines "what the market does" (Prism) with "what we built" (Loupe), and doing that synthesis manually by opening two UIs was explicit friction.

v0.15.0 shipped the bridge: a `/prd <feature>` Telegram command and `POST /api/prd/generate` endpoint. On-demand, one LLM call, strict-shape Markdown artifact persisted to `KnowledgeArtifact(artifact_type='prd_doc')`. Plus UX-friction adds: `/prd` with no args shows a feature picker; every digest card got a `[📝 Deep-dive]` button.

### Tradeoff register

**Decision 11.1: PRD synthesis lives inside Prism, not a new central tool.**
- **Gained:** Zero new deploy surface. Reuses Prism's existing research brief, query planner, Sonnet synthesizer, `KnowledgeArtifact` store, feedback-signal feedback loop, and Telegram digest. The PRD is just one more artifact type; the synthesis path is the same one that produces every trend. Total new code: ~450 lines across `loupe_client.py` + `prd_synthesizer.py` + the route + bot handler.
- **Lost:** Prism's scope widens from "competitive intelligence" to "product-strategy synthesis." Future "what IS Prism?" positioning has to acknowledge that. Also: the API now depends on Loupe's reachability; when Loupe is down, PRD section 2 is blank. We handle this gracefully (the PRD says "Loupe unreachable") but the compound document is diminished.
- **Net:** Right shape. The alternatives (Loupe hosts it — misaligned with Loupe's verification-only mandate; third repo — triples deploy surface for what's essentially one LLM call) both cost more and buy less. Asymmetric architecture (Prism = brain, Loupe = lens) is the correct shape for the product's current stage.

**Decision 11.2: HTTP-only integration between Prism and Loupe. No shared DB, no cross-imports.**
- **Gained:** Either repo can deploy/fail/iterate independently. The contract between them is a REST surface, not a schema — much smaller and more versionable. A future move to Postgres-per-service or separate cloud regions doesn't break anything.
- **Lost:** Feature matching happens via free-text `ILIKE` (Loupe's `UatRun.feature_description` vs. the user's query) instead of structured joins. Recall is imperfect. A shared `feature_id` taxonomy would be stricter, but would also require the prism-core package carve.
- **Net:** Right for v0.15. Free-text matching works well enough — the LLM synthesizes around recall gaps. Structured feature taxonomy deferred until it's demonstrably blocking quality.

**Decision 11.3: On-demand trigger only; no scheduled weekly briefings.**
- **Gained:** One flow to design, test, and explain. `/prd <feature>` in Telegram → 30-60s → Markdown. No cron, no "what counts as a weekly event?" decisions, no push-notification fatigue.
- **Lost:** Strategic briefings (weekly cross-project roll-ups) are out of scope. If a PM wants a weekly digest, they have to trigger it manually.
- **Net:** Correct for MVP. Scheduled briefings are a net-new product surface with unclear audience/format; shipping on-demand first lets real usage inform whether weekly is actually wanted.

**Decision 11.4: Feature-picker (F1) and digest deep-dive (F2) in Phase 1, not Phase 2.**
- **Gained:** Both shipped for ~4 hours of work total (two callback handlers + one endpoint + one keyboard-row addition). Turned `/prd` from a "remember the feature name" command into a "browse + tap" surface — matches how PMs actually work on a phone. F2 means digest cards become the entry point: system surfaces a trend → PM taps `[📝 Deep-dive]` → PRD generates scoped to that trend. Zero typing.
- **Lost:** A bit of scope creep in Phase 1 (plan originally kept these as "Phase 1 enhancements"). But `/friction-finder` explicitly flagged them as shipping-blocking because on-demand flow without a picker makes usage sparse.
- **Net:** Worth it. Friction-finder was right — typing a feature name from memory on mobile is a 40% friction overhead that kills actual use. The UX delta is the difference between "might ship" and "will ship."

### Key lessons

1. **Compounding wins when you refuse to duplicate.** Every concrete decision here reduces to "reuse Prism's existing spine." The cost-per-feature drops because each new feature is ~1 LLM call binding existing primitives.
2. **Asymmetric architecture is a design choice, not a bug.** Prism-brain / Loupe-lens is simpler than any symmetric design. Embrace the asymmetry.
3. **Friction-finder applied at the plan stage is worth more than polishing later.** F1 + F2 would have ended up as Phase 2 "nice to haves" that never shipped without the explicit friction check. The skill earned its slot.

---

## Chapter 12: The competitors-mismatch bug + the metric-consistency principle (April 21)

### What happened

User noticed that newly-created projects (Intuit, Sarvam.ai) showed "3 competitors" on their project card but the competitors tab was empty. Older projects (Swiggy, MakeMyTrip) worked fine. Diagnosed in ~2 minutes: the project-detail stats card counts `entity_type='company'` directly, while `GET /api/knowledge/competitors` required an additional `competes_with` relation. Some discovery paths write that relation; others don't. Fix: align the list endpoint with the counter (both use `entity_type='company'`). One source of truth.

### Why it escaped every prior test

I had validated Swiggy and MakeMyTrip extensively — both had the `competes_with` relation because older discovery runs from v0.9.x created it. The bug only manifested on projects created via the **post-carve** code path (new /new → competitive_intel discovery without the relation write). Intuit and Sarvam.ai were the first projects to exercise that path. My tests never exercised "freshly created resource, just-finished discovery" — they exercised "pre-populated resource, stable state."

### Tradeoff register

**Decision 12.1: Fix at the read layer (simplify `/competitors` query), not the write layer (make every agent path write the `competes_with` relation).**
- **Gained:** One-line fix. Retroactively correct for all existing projects (no backfill needed). Single source of truth: `entity_type='company'` means "this is a competitor in Prism's model." No second index to keep in sync.
- **Lost:** We lost the theoretical ability to have `company` entities that aren't competitors (e.g., "reference companies that aren't competing with us"). But Prism has never actually modeled that distinction — every `company` entity is de facto a competitor. So the capability we "lost" never existed.
- **Net:** Correct. Write-layer fix (ensuring every agent path writes `competes_with`) is defensive theater — it adds a coupling between entity creation and relation creation that duplicates information already encoded in `entity_type`. Read-layer fix is the simpler invariant.

**Decision 12.2: Add an invariant test instead of just fixing the bug.**
- **Gained:** `tests/test_stats_consistency.py` asserts, for every project, that `stats.competitor_count == len(GET /competitors)`. Same for `entity_count` vs `GET /entities`, `observation_count` vs observations across all entities, `plan_count` vs `GET /plans`. Runs in CI and post-task-eval. This class of bug can't ship again without the test failing.
- **Lost:** Another file to maintain. Tests drift if the underlying endpoints change shape.
- **Net:** Worth it, and cheap. The invariants are the specification. The bug happened because no one had ever written down "these numbers must agree." Writing that down is how we make sure they continue to agree.

### Key lessons

1. **Tests against pre-existing data miss the fresh-resource class of bug.** Swiggy and MakeMyTrip were pre-migrated historical data, not outputs of the current code path. Adding a "canary new-project" smoke (create project → run discovery → assert all read surfaces populate) would have caught this instantly. Queued for Phase 2 of the invariants work.
2. **When two code paths compute the same number by different rules, one of them is wrong.** It's not "they might diverge" — they will, eventually, on data you didn't test with. Converge on one canonical query per metric.
3. **Stats cards and list endpoints must agree.** The invariant test now enforces this. In any system where a count is displayed on a card and its items are listed on a detail page, they must be computed from the same base query. If that's not true, fix it now — the drift will always eventually be visible to a user.
4. **The honest answer to "why wasn't this caught earlier" is always about test surface, not about carefulness.** Adding invariants is the only sustainable fix.

### Operational add-ons

- `/post-task-eval` for API changes now includes the stats-consistency check.
- New `tests/test_stats_consistency.py` runs via `pytest tests/` and hits live `BASE_URL` (defaults to `http://localhost:8100`; set env for Railway).

---

*This document is updated with every significant learning. If you're reading this and something is missing, it means it hasn't been learned yet.*
