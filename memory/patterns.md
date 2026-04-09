# Patterns

Reusable approaches, delegation rules, and recurring solutions.

---

## Context Bandwidth Patterns

### P1: Codebase-First Delegation (before any implementation task)

**Trigger**: Any task requiring understanding of 3+ files before implementation.

**Bad pattern**:
```
main session: read file_a.py (300 lines)
main session: read file_b.py (500 lines)  
main session: read file_c.py (200 lines)
main session: spawn Agent("implement X")   ← agent re-reads same files
```

**Correct pattern**:
```
main session: spawn Explore Agent(
  "Summarize how X, Y, Z work in this codebase. 
   Focus on: entry points, data structures, key methods.
   Return under 300 words."
)
→ get 300-word summary
main session: spawn Implementation Agent(
  prompt includes the 300-word summary + specific instructions
)
```

**Savings**: Typically 1,000–3,000 lines of context.

---

### P2: Log Analysis Delegation

**Trigger**: Any command that could produce > 2KB output (logs, find, verbose test output, large diffs).

**Bad pattern**:
```
main session: Bash("railway logs --service X")  → 41KB output
main session: Bash("grep ERROR from that output") → more context
```

**Correct pattern**:
```
main session: Agent(
  "Run `railway logs --service mmt-os-bot`, 
   find the startup crash reason.
   Return: error message + root cause in under 50 words."
)
```

**Savings**: 10KB–100KB of raw output never enters main context.

---

### P3: Debug Scoping

**Trigger**: Debugging a crash/failure requires reading > 1 file or > 100 lines.

**Bad pattern**:
```
main session: read bot.py lines 1-60
main session: read bot.py lines 60-140  
main session: grep "def main" bot.py
main session: read bot.py lines 460-500
→ identified bug after 4 turns
```

**Correct pattern**:
```
main session: Agent(
  "telegram_bot/bot.py crashes at startup on Railway (container exits immediately).
   Read the file and find: (1) what fails at import/startup, (2) the exact fix.
   Return: bug + one-line fix in under 80 words."
)
→ bug found in 1 turn
```

---

### P4: Background Deployment Monitoring

**Trigger**: Waiting for any external async process (deployment, build, test suite).

**Bad pattern**:
```
main session: Bash("sleep 60 && check status")   ← turn 1
main session: Bash("sleep 60 && check status")   ← turn 2  
main session: Bash("sleep 60 && check status")   ← turn 3
main session: Bash("sleep 300 && check status")  ← turn 4
```

**Correct pattern**:
```
main session: Bash(
  "for i in $(seq 1 12); do sleep 30 && railway deployment list | grep -E 'SUCCESS|FAILED' && break; done",
  run_in_background=True
)
→ single background command; notified when done
```
Or use a background Agent to monitor + report.

**Savings**: 3–10 wasted polling turns.

---

### P5: Parallel Independent Research

**Trigger**: Any task with 3+ independent research subtasks.

**Pattern**:
```python
# Good: all in same message, run in parallel
Agent("research X", run_in_background=True)
Agent("research Y", run_in_background=True)  
Agent("research Z", run_in_background=True)
```
Never run independent research sequentially in main context.

---

### P6: Figma API — Frame Classification Heuristics

When parsing Figma files for UAT:
- Top-level FRAME nodes on a page = screens
- Frames named with `_` prefix or inside "Components/" page = skip
- Frame names containing: sheet, drawer, bottom, modal, popup = bottom_sheet type
- Frame text/components containing: "only X left", "X people", "booked", "limited", "offer", "%off" = persuasion type
- Use Claude batch analysis (all frames in ONE API call) not per-frame calls

---

### P7: Android Deployment Docker Split

Always maintain two Docker images:
- `Dockerfile.bot` — lightweight (~200MB): Python + telegram-bot + anthropic only. Runs on Railway/any cloud free tier.
- `Dockerfile` — full (~4GB): Python + Android SDK + emulator. Runs on device host with KVM.

Railway free tier cannot run the full image. Keep them separate.

---

### P8: Self-Healing Recovery Priority Order

When HealthMonitor detects a failure, always attempt recovery in this order:
1. Dismiss crash dialog (if APP_CRASHED) → relaunch
2. Force-stop + relaunch (if WRONG_SCREEN or NAVIGATION_STUCK)
3. ADB kill-server + start-server (if DEVICE_UNRESPONSIVE only)
4. Circuit break after 3 attempts → mark blocked, log gap, continue to next scenario

Never restart ADB for non-DEVICE_UNRESPONSIVE states — it's too disruptive.
