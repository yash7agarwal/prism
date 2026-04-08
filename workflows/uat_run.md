# Workflow: UAT Run

## Objective
Execute a complete UAT test for a feature across multiple accounts and builds, producing an evidence-backed report.

## Inputs
- `baseline_apk`: path to the old/reference build APK
- `candidate_apk`: path to the new build APK
- `feature_description`: natural language description of what changed
- `accounts`: list of test account credentials
- `acceptance_criteria` (optional): specific requirements to validate
- `figma_link` (optional): Figma design reference

## Outputs
- `reports/uat_report_{run_id}.md`: full UAT report
- `.tmp/evidence/{run_id}/`: screenshots and step logs per account
- Jira tickets for each REGRESSION finding (Phase 5)

## Steps

### 1. Run Initialization
- Generate a unique `run_id` (timestamp + feature slug)
- Create evidence directory structure
- Log run metadata (builds, feature, accounts, start time)

### 2. Build Installation
- Install `baseline_apk` as "old" build
- Extract package name and version
- Install `candidate_apk` as "new" build (same device, different session — or parallel emulators)

### 3. Flow Exploration (FlowExplorerAgent)
- Launch FlowExplorerAgent on the candidate build
- Input: feature description + known entry points
- Output: screen state graph (list of screens, navigation paths, key elements)
- This informs which scenarios to generate

### 4. Scenario Generation
- Input: feature description + screen graph + acceptance criteria
- Output: structured test scenario list (15–25 scenarios covering happy path, edge cases, state variants)
- Each scenario: name, preconditions, steps, expected outcome, severity if failed

### 5. Variant Detection (per account)
- Launch each account session
- Capture variant fingerprint at login (home screen hash + key modules present)
- Group accounts by variant similarity
- Tag each account with its variant group (A/B/control)

### 6. Scenario Execution (ScenarioRunnerAgents — parallel)
- For each account × scenario pair, spawn a ScenarioRunnerAgent
- Max 5 parallel agents (configurable)
- Each agent: fresh context, MCP tools, specific scenario instructions
- Returns: evidence pack (step log + screenshot paths + actual outcome)

### 7. Build Comparison (DiffAgent)
- For each key screen, compare baseline vs candidate screenshots
- Returns: diff images, list of visual differences, severity

### 8. Evaluation (EvaluatorAgent)
- For each scenario result: classify as PASS / FAIL / PARTIAL / VARIANT_DIFFERENCE
- Cross-reference variant groups before marking as REGRESSION
- Only mark REGRESSION if same-variant accounts fail consistently

### 9. Report Assembly (ReportWriterAgent)
- Merge all subagent outputs
- Structure per report template (sections A–I)
- Include variant analysis section
- Output: Markdown report + Jira defect list

## Edge Cases

- **Emulator crashes**: Restart emulator, retry failed scenario once, mark as FLAKY if fails again
- **Login failure**: Skip account, log as BLOCKED, continue with remaining accounts
- **App hangs**: Force stop + relaunch from known entry point, retry once
- **Network errors**: Retry with 3s backoff, mark as ENV_ISSUE if persists

## Quality Bar

A UAT run is complete when:
- All scenarios executed (or explicitly skipped with reason)
- All FAIL results have screenshot evidence
- Variant analysis section populated
- Report reviewed and findings classified
