# Changelog

All notable changes are documented here following [Semantic Versioning](https://semver.org/).

## [0.2.0] — 2026-04-09
### Added
- `run_details_uat.py` — fully autonomous hotel details page UAT runner for 10.7.0 vs 11.3.0 comparison
- Screen state verification (`get_screen_state`) using live UI tree: detects `on_mmt`, `on_details_page`, `gallery_cleared`
- Autonomous app launch (`launch_mmt`) + hotel navigation (`navigate_to_hotel_details`) — no manual steps required
- `ensure_on_details_page` pre-flight: launches MMT and navigates to hotel before handing control to Claude
- Agent tools: `check_screen` (returns live state JSON), `open_mmt_app`, `scroll_fast` (gallery escape), `scroll_down` with new-content detection
- `scroll_fast` uses safe mid-screen swipe coords (65%→30%) to avoid Android home gesture zone
- `consecutive_no_new_content` auto-stop: ends capture after 3 empty scrolls
- UAT report generation inline in `run_report()` with visual diff appendix
- `--hotel` CLI arg to specify hotel search query per run

### Changed
- `tools/android_device.py`: `tap()` switched to `adb shell input tap` to fix INJECT_EVENTS on MIUI/Motorola
- `tools/android_device.py`: `swipe()` switched to `adb shell input swipe` with explicit coordinate mapping
- `tools/android_device.py`: `swipe_coords()` switched to `adb shell input swipe`

## [0.1.0] — 2026-04-09
### Added
- Full AOS layer scaffold: tools/, workflows/, memory/, utils/, config/
- MCP server with 13 Android device control tools (screenshot, tap, swipe, get_ui_tree, install_apk, launch_app, etc.)
- AndroidDevice wrapper over uiautomator2 with tap, swipe, type, UI tree, screenshot
- APK manager using ADB + aapt for install, launch, version extraction
- Multi-agent UAT orchestration: OrchestratorAgent, FlowExplorerAgent, ScenarioRunnerAgent
- A/B variant detector: fingerprints post-login home screen, groups accounts by variant, classifies REGRESSION vs VARIANT_DIFFERENCE
- Build comparison layer: visual_diff (pixelmatch + PIL fallback), DiffAgent, EvaluatorAgent
- ReportWriterAgent assembling full structured UAT Markdown reports
- report_generator.py: Jira defect list, Slack summary, JSON export
- EvidenceCapture for timestamped screenshot + step log management
- Seed memory files: learnings, patterns, decisions, user_context (MMT product context + account registry)
- UAT run and flow discovery workflow SOPs
- smoke_test.py validating config, Claude API, ADB, uiautomator2, screenshot, MCP server
- setup_emulator.sh for one-time Android AVD creation
- Python 3.11 venv with all dependencies (mcp, uiautomator2, pillow, pixelmatch, fastapi, anthropic)
