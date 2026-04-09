# MMT-OS · v0.4.0

> AI-native Android UAT system for MakeMyTrip — autonomous testing, Figma-first design validation, self-healing execution, and Telegram-based remote control.

MMT-OS is a multi-agent operating system that runs end-to-end UAT on MakeMyTrip Android builds without manual intervention. Upload an APK and a Figma URL via Telegram — the system parses the full design journey, navigates the app to every screen, compares against Figma frames, and delivers a per-screen compliance report. No baseline APK needed.

---

## What It Does

- **Figma-first UAT** — parses Figma file to extract all screens, sheets, persuasion elements, and CTAs; generates test cases from the design spec; navigates the app to each screen and compares
- **Autonomous flow exploration** — maps screens and UI elements for a given feature automatically
- **AI-generated test scenarios** — Claude generates 10–20 scenarios from feature description + screen graph
- **Self-healing execution** — detects and recovers from crashes, stuck navigation, wrong screens, and unresponsive devices; logs all recovery events to `memory/gaps_log.jsonl`
- **A/B variant detection** — fingerprints accounts by post-login variant; classifies failures as REGRESSION vs VARIANT_DIFFERENCE
- **Use case registry** — pre-flight coverage gate ensures all registered use cases are covered before a run
- **Cloud-ready emulator** — boots headless Android AVD, auto-installs APK, handles fresh device cold-start for CI
- **Telegram bot** — `/run`, `/run_figma`, `/status`, `/report`, `/list`, `/cases` from your phone; deployed 24/7 on Railway
- **MCP server** — 13 tools exposing Android device control to Claude Code

---

## Architecture

```
Telegram Bot (Railway, always-on)    Mac / Device Host
┌──────────────────────┐             ┌────────────────────────────────────────┐
│  /run_figma <url>    │────────────▶│  FigmaJourneyParser                    │
│  /run <feature>      │             │  └─ parse() → JourneySpec              │
│  [upload .apk]       │             │                                        │
└──────────────────────┘             │  Orchestrator.run_figma_uat()          │
                                     │  ├─ FigmaUATRunner (navigate+compare)  │
                                     │  ├─ HealthMonitor  (self-healing)      │
                                     │  ├─ FlowExplorerAgent (screen map)     │
                                     │  ├─ UseCaseRegistry (pre-flight gate)  │
                                     │  ├─ ScenarioRunnerAgent × N            │
                                     │  ├─ VariantDetector (A/B grouping)     │
                                     │  ├─ FigmaComparator (Claude vision)    │
                                     │  ├─ EvaluatorAgent                     │
                                     │  └─ ReportWriterAgent → reports/*.md   │
                                     │                                        │
                                     │  AndroidDevice (uiautomator2 + ADB)    │
                                     │  EmulatorManager (headless AVD)        │
                                     └────────────────────────────────────────┘
```

---

## Project Structure

```
MMT-OS/
├── agent/
│   ├── orchestrator.py          # Main coordinator + run_figma_uat() entry point
│   ├── figma_journey_parser.py  # Parses Figma file → journey spec + test cases
│   ├── figma_uat_runner.py      # Navigates app to each Figma screen + compares
│   ├── figma_comparator.py      # Claude vision diff: screenshot vs Figma frame
│   ├── health_monitor.py        # Self-healing: detects + recovers failure states
│   ├── use_case_registry.py     # Use case store + pre-flight coverage gate
│   ├── flow_explorer_agent.py   # Maps app screens via Claude tool loop
│   ├── scenario_runner_agent.py # Executes one scenario via Claude + ADB tools
│   ├── variant_detector.py      # A/B fingerprinting + REGRESSION classification
│   ├── diff_agent.py            # Build comparison + Figma validation mode
│   ├── evaluator_agent.py       # Scores scenario results
│   ├── report_writer_agent.py   # Generates Markdown UAT reports
│   └── run_uat.py               # CLI entry point
├── tools/
│   ├── android_device.py        # uiautomator2 + ADB device wrapper
│   ├── apk_manager.py           # ADB/aapt install, launch, version extraction
│   ├── emulator_manager.py      # Cloud-ready AVD lifecycle management
│   ├── visual_diff.py           # Pixel-level screenshot comparison
│   ├── screenshot.py            # Evidence capture (timestamped screenshots)
│   └── report_generator.py      # Jira, Slack, JSON export helpers
├── telegram_bot/
│   ├── bot.py                   # Async bot: /run /run_figma /status /report /list
│   └── run_bot.py               # Entry point: python -m telegram_bot.run_bot
├── mcp_server/
│   └── server.py                # FastMCP server (13 Android control tools)
├── memory/
│   ├── use_cases.json           # Persistent use case registry
│   ├── gaps_log.jsonl           # Self-healing gap log
│   ├── learnings.md             # Operational learnings (8 entries)
│   ├── patterns.md              # Reusable delegation patterns (8 entries)
│   ├── decisions.md             # Architecture decision log
│   └── user_context.md          # MMT product context + test accounts
├── workflows/
│   ├── context_efficiency.md    # Pre-task delegation checklist + agent templates
│   ├── uat_run.md               # UAT execution SOP
│   └── flow_discovery.md        # Screen exploration protocol
├── config/settings.yaml         # All tunable parameters
├── reports/                     # Generated UAT + Figma compliance reports
├── apks/                        # APK uploads (candidate.apk)
├── Dockerfile                   # Full image with Android SDK (device host)
├── Dockerfile.bot               # Lightweight bot-only image for Railway (~200MB)
├── docker-compose.yml           # Full stack with KVM passthrough
├── railway.json                 # Railway deploy config (uses Dockerfile.bot)
├── requirements.txt             # Full dependencies
├── requirements.bot.txt         # Bot-only dependencies
└── smoke_test.py                # Validates Claude API, ADB, device, MCP
```

---

## Setup

**Prerequisites:** Python 3.11+, Android SDK (for device runs), Java 17 (for emulator)

```bash
# 1. Clone and create venv
git clone https://github.com/yash7agarwal/MMT-OS.git
cd MMT-OS
python3.11 -m venv .venv && source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Fill in: ANTHROPIC_API_KEY, TELEGRAM_BOT_TOKEN, FIGMA_ACCESS_TOKEN

# 4. (First time) Set up Android emulator
bash setup_emulator.sh

# 5. Validate stack
python smoke_test.py
```

---

## Usage

| Task | Command |
|------|---------|
| Figma-first UAT (CLI) | `python -c "from agent.orchestrator import Orchestrator; Orchestrator.run_figma_uat('https://figma.com/design/...', 'apks/candidate.apk', [])"` |
| Standard UAT (CLI) | `python agent/run_uat.py --candidate apks/candidate.apk --feature "hotel search" --accounts accounts.json` |
| Cloud cold-start | `python -c "from agent.orchestrator import Orchestrator; Orchestrator.run_cold_start('apks/candidate.apk', 'hotel search', [])"` |
| Start Telegram bot | `python -m telegram_bot.run_bot` |
| Start MCP server | `python mcp_server/server.py` |

### Telegram Commands

| Command | Description |
|---------|-------------|
| `/run_figma <figma_url>` | Parse Figma + run design compliance UAT |
| `/run <feature>` | Standard scenario-based UAT run |
| `/status` | Show current run status |
| `/report` | Send latest report |
| `/list` | List recent runs with pass rates |
| `/cases <feature>` | Show registered use cases |
| `/help` | List all commands |

**Figma flow:** Upload `.apk` → bot asks for Figma URL → paste URL → UAT runs automatically.

---

## Configuration

| Variable | Description | Where to get it |
|----------|-------------|-----------------|
| `ANTHROPIC_API_KEY` | Claude API key | console.anthropic.com |
| `TELEGRAM_BOT_TOKEN` | Bot token | @BotFather on Telegram |
| `FIGMA_ACCESS_TOKEN` | Figma personal token | figma.com → Settings → Access tokens |
| `DEVICE_SERIAL` | ADB device serial (optional) | `adb devices` |
| `UAT_ACCOUNTS_FILE` | Path to accounts JSON | create manually |
| `UAT_FEATURE` | Default feature for `/run` | set to your feature name |

All agent tuning parameters live in `config/settings.yaml`.

---

## Cloud Deploy (Railway)

```bash
brew install railway && railway login
railway init && railway service
railway variable set ANTHROPIC_API_KEY=... TELEGRAM_BOT_TOKEN=... FIGMA_ACCESS_TOKEN=...
railway up --service mmt-os-bot
```

`Dockerfile.bot` (~200MB) runs the Telegram interface always-on. UAT execution runs on a device-connected host.

---

## Changelog

### [0.4.0] — 2026-04-09
- `figma_journey_parser.py`: parses Figma file into journey spec; classifies main/sheet/persuasion/modal frames; batched Claude enrichment for navigation steps + assertions
- `figma_uat_runner.py`: navigates app to each Figma screen, screenshots, compares, checks assertions, writes compliance report
- Telegram `/run_figma` command + auto-detect Figma URLs after APK upload
- `Orchestrator.run_figma_uat()` classmethod — no baseline APK needed
- Context efficiency module: learnings, patterns, workflow, CLAUDE.md gate

### [0.3.0] — 2026-04-09
- Self-healing engine, cloud emulator manager, use case registry, Figma comparator
- Telegram bot deployed on Railway (Dockerfile.bot, ~200MB)

### [0.2.0] — 2026-04-09
- Autonomous hotel details UAT runner; screen state verification; ADB tap/swipe fix

### [0.1.0] — 2026-04-09
- Initial system: MCP server, multi-agent orchestration, A/B variant detection, report generation

---

## Roadmap

- **Phase 4**: Web dashboard (FastAPI + Jinja2) — build upload, live run monitor, report viewer, use case editor
- **Phase 5**: Jira auto-filing, Slack notifications, memory compounding across runs
- Login automation (auto-login per account, no pre-logged-in sessions required)
- Multi-device parallelism (distribute accounts across devices for faster runs)
