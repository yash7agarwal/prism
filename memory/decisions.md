# Decisions

Key architectural and operational decision rationale.

---

## 2026-04-08 — Android-first, MCP-first architecture

**Decision**: Start with Android (not iOS), use MCP server as the Claude-device interface, multi-agent orchestration to preserve context bandwidth.

**Why**: Android has better programmatic automation tooling (UIAutomator2, ADB). MCP is the standard Claude tool interface. Multi-agent prevents context overflow across 20+ screen flows and 5+ accounts.

**Tradeoffs**: iOS support deferred to Phase 3. Web dashboard deferred to Phase 4.
