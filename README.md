# MMT-OS (Product OS) · v0.1.0

> AI-native operating system for MakeMyTrip product managers — starting with an autonomous Android app UAT tool.

MMT-OS is a compounding intelligence system that autonomously tests Android app builds, compares versions, detects regressions vs A/B variant differences, and generates evidence-backed UAT reports. Built on the AOS (Agent Operating System) philosophy: every run makes the system smarter.

---

## What It Does

- **Autonomous Android UAT** — installs two APK builds, explores flows, executes test scenarios, captures screenshots
- **A/B Variant Detection** — fingerprints post-login UI per account, groups by variant, prevents false regression reports
- **Multi-agent architecture** — orchestrator spawns parallel subagents (one per scenario × account), preserving context bandwidth
- **Build comparison** — visual diff (pixelmatch) between baseline and candidate screenshots, per-screen change classification
- **Evidence-backed reports** — structured Markdown UAT reports with scenario matrix, defect log, variant analysis, build diff
- **MCP server** — 13 tools exposing Android device control to Claude (tap, swipe, screenshot, UI tree, APK install)
- **Jira/Slack export** — converts defects to Jira-ready issue dicts; generates Slack summary blocks
- **Compounding memory** — learnings, patterns, decisions, and account variant history stored and reused across runs

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   PM Interface                       │
│           CLI (run_uat.py) · Web (Phase 4)          │
└─────────────────────┬───────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────┐
│              Orchestrator (agent/orchestrator.py)    │
│  installs APKs · generates scenarios · collects      │
└──┬──────────────┬──────────────┬────────────────────┘
   │              │              │
┌──▼──┐      ┌───▼───┐     ┌────▼─────┐
│Flow │      │Scenario│     │ Variant  │
│Expl │      │Runner  │     │ Detector │
│Agent│      │Agent×N │     │          │
└──┬──┘      └───┬───┘     └────┬─────┘
   │              │              │
   └──────────────▼──────────────┘
              Evidence Packs
                   │
   ┌───────────────▼──────────────────┐
   │   DiffAgent · EvaluatorAgent     │
   │   ReportWriterAgent              │
   └───────────────┬──────────────────┘
                   │
┌──────────────────▼──────────────────┐
│         MCP Server (13 tools)        │
│  screenshot · tap · swipe · ui_tree  │
│  install_apk · launch_app · ...      │
└──────────────────┬──────────────────┘
                   │
┌──────────────────▼──────────────────┐
│        Android Device / Emulator     │
│        (uiautomator2 + ADB)          │
└─────────────────────────────────────┘
```

---

## Project Structure

```
MMT-OS/
├── agent/
│   ├── orchestrator.py          # Main UAT coordinator
│   ├── run_uat.py               # CLI entry point
│   ├── flow_explorer_agent.py   # Maps app screens via Claude tool loop
│   ├── scenario_runner_agent.py # Executes one scenario × one account
│   ├── variant_detector.py      # A/B fingerprinting + grouping
│   ├── diff_agent.py            # Baseline vs candidate comparison
│   ├── evaluator_agent.py       # PASS/FAIL/PARTIAL/VARIANT_DIFFERENCE
│   └── report_writer_agent.py   # Final report assembly
├── mcp_server/
│   └── server.py                # FastMCP server (13 device tools)
├── tools/
│   ├── android_device.py        # uiautomator2 wrapper
│   ├── apk_manager.py           # ADB/aapt APK management
│   ├── screenshot.py            # EvidenceCapture with step logs
│   ├── visual_diff.py           # Screenshot comparison
│   └── report_generator.py      # Jira, Slack, JSON export
├── utils/
│   ├── claude_client.py         # Anthropic SDK wrapper
│   └── config.py                # YAML settings loader
├── workflows/
│   ├── uat_run.md               # Full UAT execution SOP
│   └── flow_discovery.md        # Screen exploration protocol
├── memory/
│   ├── user_context.md          # MMT product context + account registry
│   ├── decisions.md             # Architecture decision log
│   ├── learnings.md             # Operational insights
│   └── patterns.md              # Reusable patterns
├── config/settings.yaml         # All configuration
├── smoke_test.py                # Stack validation script
├── setup_emulator.sh            # One-time AVD setup
├── requirements.txt
└── .env.example
```

---

## Setup

```bash
# 1. Clone and enter project
cd /Users/yash/ClaudeWorkspace/MMT-OS

# 2. Create Python 3.11 venv and install deps
python3.11 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 3. Copy env and add your API key
cp .env.example .env
# edit .env → set ANTHROPIC_API_KEY

# 4a. Physical device: enable USB Debugging → plug in via USB
# 4b. Emulator: install Java first, then:
bash setup_emulator.sh
~/Library/Android/sdk/emulator/emulator -avd mmt_test &

# 5. Initialize uiautomator2 on device
.venv/bin/python3 -m uiautomator2 init

# 6. Validate stack
.venv/bin/python3 smoke_test.py
```

---

## Usage

| Task | Command |
|------|---------|
| Run full UAT (single build) | `.venv/bin/python3 agent/run_uat.py --candidate new.apk --feature "hotel gallery" --accounts accounts.json` |
| Run build comparison | `.venv/bin/python3 agent/run_uat.py --baseline old.apk --candidate new.apk --feature "checkout coupon" --accounts accounts.json` |
| Start MCP server (for Claude Code) | `.venv/bin/python3 mcp_server/server.py` |
| Validate stack | `.venv/bin/python3 smoke_test.py` |

**accounts.json format:**
```json
[
  {"id": "acc1", "type": "returning_user"},
  {"id": "acc2", "type": "new_user"},
  {"id": "acc3", "type": "premium_user"}
]
```

Reports are saved to `reports/uat_report_{run_id}.md`.

---

## Configuration

| Variable | Description | Source |
|----------|-------------|--------|
| `ANTHROPIC_API_KEY` | Claude API key | console.anthropic.com |
| `DEVICE_SERIAL` | ADB device serial (optional) | `adb devices` |
| `FIGMA_ACCESS_TOKEN` | Figma API token (Phase 5) | figma.com/developers |
| `agent.max_parallel_runners` | Max concurrent ScenarioRunnerAgents | `config/settings.yaml` |
| `agent.exploration_depth` | Max screens per flow exploration | `config/settings.yaml` |

---

## Changelog

### [0.1.0] — 2026-04-09
- Initial release: full agent stack (Phases 1–3)
- MCP server with 13 Android device tools
- Multi-agent UAT orchestration with A/B variant detection
- Build comparison + evidence-backed report generation

---

## Roadmap

- **Phase 4** — Web dashboard: build upload, run monitor, report viewer (FastAPI + Jinja2)
- **Phase 5** — Jira auto-filing, Figma design validation, Slack summaries, memory compounding
- **Phase 6** — iOS support, analytics validation, experiment-aware regression detection
- **Phase 7** — Full Product OS: PRD generation, competitor research, conversation → ticket conversion
