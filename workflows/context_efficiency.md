# Workflow: Context-Efficient Agent Operation

## Objective
Execute every task with minimal main-session context consumption by delegating reading, analysis, debugging, and monitoring to subagents.

## Inputs
- A task or feature request

## Outputs
- Task completed
- Main session context used only for decisions, coordination, and user communication

---

## Pre-Task Checklist (run mentally before starting)

Before doing ANYTHING, answer these:

1. **Does this task require reading 3+ files?**
   → YES: Spawn Explore agent first. Get a 300-word summary. Never read those files in main session.

2. **Will any command produce > 2KB of output?**
   → YES: Delegate to a subagent with a specific question. Never read raw output inline.

3. **Are there 3+ independent subtasks?**
   → YES: Spawn all in parallel in a single message with `run_in_background=True`.

4. **Will I need to wait for an external process (deploy, build, test)?**
   → YES: Use `run_in_background=True` on a single monitoring command. Never poll in a loop.

5. **Is this debugging a crash/failure?**
   → YES: Spawn a debug subagent with the exact symptom. Never chunk-read files inline.

---

## Decision Tree

```
New task arrives
    │
    ├─ Needs to read files? ──YES──▶ How many files / how many lines?
    │                                    ├─ 1 file, < 100 lines → Read inline (OK)
    │                                    └─ 2+ files OR > 100 lines → Explore agent
    │
    ├─ Needs to run commands? ──YES──▶ How much output?
    │                                    ├─ < 2KB expected → Run inline (OK)
    │                                    └─ > 2KB expected → Delegate to agent
    │
    ├─ Needs to wait? ──YES──▶ Background command or background agent
    │
    ├─ Implementation task? ──YES──▶ Explore first → then Implementation agent
    │
    └─ All inline reads < 100 lines AND no waiting → Proceed inline
```

---

## Standard Agent Templates

### Template A: Codebase Pre-Read (before any implementation)
```
Agent(
  subagent_type="Explore",
  prompt="""
  Before implementing [FEATURE], summarize the relevant parts of the codebase.
  
  Focus on:
  - Entry points related to [FEATURE]
  - Data structures / class signatures involved
  - Key methods I'll need to call or modify
  - Any constraints or patterns in the existing code
  
  Files to look at: [list suspected files or just the directory]
  
  Return: under 300 words, structured as bullet points per file.
  """
)
```

### Template B: Log Analysis
```
Agent(
  prompt="""
  Run: [command that produces logs]
  
  Find: why [specific thing] failed / crashed / didn't work.
  
  Return: root cause in under 50 words + the exact fix if clear.
  """
)
```

### Template C: Debug Triage
```
Agent(
  prompt="""
  [File/component] is crashing with [symptom].
  
  Read [file path] and identify:
  1. The root cause of the crash
  2. The exact fix (code change)
  
  Return: bug description + fix in under 100 words.
  Do NOT fix it yourself — just report back.
  """
)
```

### Template D: Deployment Monitor (background)
```
Bash(
  "for i in $(seq 1 20); do sleep 30; STATUS=$(railway deployment list --service X | grep -E 'SUCCESS|FAILED'); if [ -n \"$STATUS\" ]; then echo $STATUS; break; fi; done",
  run_in_background=True
)
```

### Template E: Parallel Implementation
```python
# Send ALL in a SINGLE message to run in parallel
Agent("implement component A — details...", run_in_background=True)
Agent("implement component B — details...", run_in_background=True)
Agent("implement component C — details...", run_in_background=True)
# Then wait for all three notifications before continuing
```

---

## What MUST Stay in Main Session

- Deciding which agents to spawn (task decomposition)
- Reading agent results and synthesizing
- Communicating decisions/status to the user
- Final small targeted edits (< 20 lines) after agent reports
- Commits and git operations

---

## Post-Task: Run Context Audit

After any session involving 3+ agents or 10+ tool calls, run `/context-audit` to:
- Score the session's efficiency
- Log any new waste patterns
- Update memory/learnings.md and memory/patterns.md

This creates the compounding loop: each session makes the system smarter about when to delegate.

---

## Quality Bar

| Metric | Target |
|--------|--------|
| Main session reads > 200 lines per task | 0 |
| Command outputs > 2KB read inline | 0 |
| Polling loops > 2 turns | 0 |
| Same file read twice in main session | 0 |
| Session grade (from /context-audit) | A or B |
