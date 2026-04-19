# Prism — Lessons Learned & Project Chronicle

> A living document that captures every pivot, blocker, breakthrough, and lesson from building Prism. Updated every session. Read this to understand not just WHAT was built, but WHY every decision was made and what went wrong along the way.

**Last updated:** 2026-04-18 (late evening session)

---

## Timeline Overview

| Date | Phase | Key Event |
|------|-------|-----------|
| 2026-04-09 | v0.1-0.3 | Original MMT-OS: UAT-only tool for MakeMyTrip |
| 2026-04-10-12 | v0.4-0.7.1 | Vision navigation, Figma UAT, web app, self-healing |
| 2026-04-16 | v0.8.0 | **Pivot**: UAT tool → Product Intelligence OS with multi-agent system |
| 2026-04-17 | v0.8.1 | Unified platform, "Prism" rebrand, Telegram /new command |
| 2026-04-18 | v0.9.0 | Lenses, Impact Engine, Trends, Groq integration, efficiency rewrite |

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

*This document is updated with every significant learning. If you're reading this and something is missing, it means it hasn't been learned yet.*
