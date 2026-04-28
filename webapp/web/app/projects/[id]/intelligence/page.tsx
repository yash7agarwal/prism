'use client'

import { useEffect, useState, useCallback } from 'react'
import Link from 'next/link'
import {
  Brain,
  Eye,
  MagnifyingGlass,
  ChartLine,
  GitBranch,
  Lightning,
  Clock,
  CheckCircle,
  Warning,
  XCircle,
  CircleNotch,
  ArrowClockwise,
  Info,
} from '@phosphor-icons/react'
import { api } from '@/lib/api'
import type { ProductOSStatus, KnowledgeSummary, AgentSession, WorkItem } from '@/lib/types'
import { ErrorBanner } from '@/components/ErrorBanner'

// v0.18.5: keys must be top-level orchestrator agent_types. The orchestrator
// only knows: intel, ux_intel, impact_analysis, digest. competitive_intel
// and industry_research are *legs* of the intel agent — clicking either
// dispatched a 404 silently. Both cards now route to `intel` which runs
// both legs in sequence; we keep them visually separate because the
// surface concepts are distinct enough to deserve their own cards.
const AGENTS = [
  {
    key: 'intel',
    label: 'Competitive Intelligence',
    icon: Eye,
    color: 'amber',
    description: 'Discovers competitors (local + global category leaders + indirect substitutes), researches features, pricing, and recent moves. Runs alongside Industry Research as part of the same `intel` agent — clicking either card kicks off the same job.',
    examples: 'Recent output: competitor profiles, feature comparisons, pricing analyses',
  },
  {
    key: 'intel',
    label: 'Industry Research',
    icon: MagnifyingGlass,
    color: 'sky',
    description: 'Tracks industry trends, regulatory changes, market data, and analyst reports. Runs alongside Competitive Intelligence as part of the same `intel` agent — clicking either card kicks off the same job.',
    examples: 'Recent output: trend reports, regulatory alerts, market sizing',
  },
  {
    key: 'ux_intel',
    label: 'UX Intelligence',
    icon: ChartLine,
    color: 'violet',
    description: 'Maps app flows by navigating the actual Android app. Compares UX patterns across competitors. Requires a connected device.',
    examples: 'Recent output: flow maps, UX comparison screenshots, journey documentation',
  },
  {
    key: 'impact_analysis',
    label: 'Impact Analysis',
    icon: GitBranch,
    color: 'rose',
    description: 'Traces how macro trends cascade into 2nd and 3rd order effects on competitors.',
    examples: 'Recent output: trend cascades, company-specific impact assessments',
  },
]

function Tooltip({ text }: { text: string }) {
  const [show, setShow] = useState(false)
  return (
    <span className="relative inline-flex ml-1">
      <button onMouseEnter={() => setShow(true)} onMouseLeave={() => setShow(false)} className="text-zinc-600 hover:text-zinc-400">
        <Info size={12} />
      </button>
      {show && (
        <span className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 w-56 bg-zinc-800 border border-zinc-700 text-zinc-300 text-xs rounded-lg p-2.5 z-50 leading-relaxed">
          {text}
        </span>
      )}
    </span>
  )
}

export default function IntelligencePage({ params }: { params: { id: string } }) {
  const projectId = parseInt(params.id, 10)

  const [status, setStatus] = useState<ProductOSStatus | null>(null)
  const [workItems, setWorkItems] = useState<WorkItem[]>([])
  const [sessions, setSessions] = useState<AgentSession[]>([])
  const [loading, setLoading] = useState(true)
  const [runningAgent, setRunningAgent] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const fetchAll = useCallback(async () => {
    try {
      const [s, items, sess] = await Promise.all([
        api.productOSStatus(projectId),
        api.listWorkItems(projectId),
        api.listSessions(projectId),
      ])
      setStatus(s)
      setWorkItems(items)
      setSessions(sess)
      setError(null)
    } catch (err) {
      setError((err as Error).message || String(err))
    }
    setLoading(false)
  }, [projectId])

  useEffect(() => { fetchAll() }, [fetchAll])

  // Auto-refresh while any agent is running. v0.18.6: tightened from 8s
  // to 3s so the live activity panel actually feels live, and we keep
  // polling for 30s after the last in-progress item disappears so the
  // user sees the final completed state without manual refresh.
  const hasActiveWork = workItems.some(i => i.status === 'in_progress')
  useEffect(() => {
    if (!hasActiveWork && !runningAgent) return
    const iv = setInterval(fetchAll, 3000)
    return () => clearInterval(iv)
  }, [hasActiveWork, runningAgent, fetchAll])

  // Clear runningAgent when data shows the agent is no longer active
  useEffect(() => {
    if (runningAgent && !hasActiveWork) {
      // Give it a moment — the work item might not be in_progress yet
      const timer = setTimeout(() => {
        const stillActive = runningAgent === 'all'
          ? workItems.some(i => i.status === 'in_progress')
          : workItems.some(i => i.agent_type === runningAgent && i.status === 'in_progress')
        if (!stillActive) setRunningAgent(null)
      }, 15000) // 15s grace period for the agent to start
      return () => clearTimeout(timer)
    }
  }, [runningAgent, hasActiveWork, workItems])

  const handleRun = async (agentType: string) => {
    setRunningAgent(agentType)
    setError(null)
    try {
      await api.runAgent(projectId, agentType)
      // Poll quickly to pick up the in_progress state
      setTimeout(fetchAll, 2000)
      setTimeout(fetchAll, 5000)
      setTimeout(fetchAll, 12000)
    } catch (e) {
      // v0.18.5: silent catches were hiding the real cause (e.g. 404 on
      // unknown agent_type). Surface the error so users see WHY a run
      // didn't start instead of a button that just resets.
      setRunningAgent(null)
      setError((e as Error).message || `Failed to start ${agentType}`)
    }
  }

  const handleRunAll = async () => {
    setRunningAgent('all')
    setError(null)
    try {
      await api.runAllAgents(projectId)
      setTimeout(fetchAll, 2000)
      setTimeout(fetchAll, 5000)
      setTimeout(fetchAll, 12000)
    } catch (e) {
      setRunningAgent(null)
      setError((e as Error).message || 'Failed to start agents')
    }
  }

  const timeAgo = (ts: string) => {
    const diff = Date.now() - new Date(ts).getTime()
    const mins = Math.floor(diff / 60000)
    if (mins < 1) return 'just now'
    if (mins < 60) return `${mins}m ago`
    const hrs = Math.floor(mins / 60)
    if (hrs < 24) return `${hrs}h ago`
    return `${Math.floor(hrs / 24)}d ago`
  }

  if (loading) {
    return (
      <div className="space-y-4">
        {[0, 1, 2].map(i => (
          <div key={i} className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
            <div className="skeleton h-5 w-48 mb-3" />
            <div className="skeleton h-4 w-full mb-2" />
            <div className="skeleton h-4 w-32" />
          </div>
        ))}
      </div>
    )
  }

  // v0.18.6: live activity feed — what's running RIGHT NOW + recent.
  const inProgressNow = workItems.filter(i => i.status === 'in_progress')
  const recentlyCompleted = [...workItems]
    .filter(i => i.status === 'completed' || i.status === 'failed')
    .sort((a, b) => (b.completed_at || '').localeCompare(a.completed_at || ''))
    .slice(0, 5)

  const fmtElapsed = (startedAt: string | null): string => {
    if (!startedAt) return ''
    const ms = Date.now() - new Date(startedAt).getTime()
    if (ms < 0 || isNaN(ms)) return ''
    const s = Math.floor(ms / 1000)
    if (s < 60) return `${s}s`
    return `${Math.floor(s / 60)}m ${s % 60}s`
  }

  return (
    <div className="space-y-4">
      {error && <ErrorBanner message={error} />}
      {/* Page intro + run all */}
      <div className="flex items-center justify-between">
        <p className="text-sm text-zinc-500">
          Each agent researches a different area. Run them individually or all at once — they work in parallel.
        </p>
        <button
          onClick={handleRunAll}
          disabled={runningAgent === 'all' || hasActiveWork}
          className="shrink-0 inline-flex items-center gap-1.5 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-40 disabled:cursor-not-allowed text-white px-4 py-2 rounded-lg text-sm font-medium transition-colors"
        >
          {runningAgent === 'all' ? (
            <><CircleNotch size={14} className="animate-spin" /> Starting...</>
          ) : (
            <><Lightning size={14} weight="fill" /> Run all agents</>
          )}
        </button>
      </div>

      {/* v0.18.6: live activity panel — shown whenever something is running
           or recently finished. Replaces the old "Starting..." button-only
           feedback that left users staring at a spinner with no detail. */}
      {(inProgressNow.length > 0 || (runningAgent && !hasActiveWork)) && (
        <div className="bg-zinc-900 border border-emerald-500/30 rounded-xl p-4">
          <div className="flex items-center gap-2 mb-3">
            <CircleNotch size={14} className="animate-spin text-emerald-400" />
            <span className="text-sm font-medium text-zinc-200">
              {inProgressNow.length > 0
                ? `${inProgressNow.length} item${inProgressNow.length > 1 ? 's' : ''} running`
                : 'Starting agent…'}
            </span>
            <span className="text-xs text-zinc-500 ml-auto">refreshes every 3s</span>
          </div>
          {inProgressNow.length === 0 && (
            <div className="text-xs text-zinc-500 italic pl-6">
              Agent thread spawned. Searching, fetching pages, calling LLM.
              First work item will appear here in 5–15s. If nothing shows in
              30s, check the Backlog tab — the run may have stalled on
              quota.
            </div>
          )}
          {inProgressNow.map((item) => (
            <div key={item.id} className="flex items-start gap-2 py-1.5 border-t border-zinc-800/50 first:border-0">
              <CircleNotch size={11} className="animate-spin text-emerald-400 shrink-0 mt-1" />
              <div className="min-w-0 flex-1">
                <div className="text-xs text-zinc-500 font-mono uppercase tracking-wider">
                  {item.agent_type} · {item.category}
                </div>
                <div className="text-sm text-zinc-200 line-clamp-2">
                  {item.description || `(no description) — work item #${item.id}`}
                </div>
              </div>
              <div className="text-xs text-zinc-500 font-mono shrink-0 mt-0.5">
                {fmtElapsed(item.started_at)}
              </div>
            </div>
          ))}
          {recentlyCompleted.length > 0 && (
            <details className="mt-3 pt-3 border-t border-zinc-800">
              <summary className="text-xs text-zinc-500 cursor-pointer hover:text-zinc-300">
                Last {recentlyCompleted.length} finished
              </summary>
              <div className="mt-2 space-y-1">
                {recentlyCompleted.map((item) => (
                  <div key={item.id} className="flex items-start gap-2 text-xs">
                    {item.status === 'completed' ? (
                      <CheckCircle size={11} className="text-emerald-500 shrink-0 mt-0.5" weight="fill" />
                    ) : (
                      <XCircle size={11} className="text-red-400 shrink-0 mt-0.5" weight="fill" />
                    )}
                    <div className="min-w-0 flex-1">
                      <span className="text-zinc-400 font-mono">{item.agent_type}/{item.category}</span>
                      {item.result_summary && (
                        <span className="text-zinc-500"> — {item.result_summary.slice(0, 120)}</span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </details>
          )}
        </div>
      )}

      {/* Agent cards */}
      {AGENTS.map((agent) => {
        const Icon = agent.icon
        const agentItems = workItems.filter(i => i.agent_type === agent.key)
        const activeItem = agentItems.find(i => i.status === 'in_progress')
        const pendingItems = agentItems.filter(i => i.status === 'pending')
        const completedItems = agentItems.filter(i => i.status === 'completed')
        const failedItems = agentItems.filter(i => i.status === 'failed')
        const lastSession = sessions.find(s => s.agent_type === agent.key)
        const isStarting = runningAgent === agent.key && !activeItem

        // Determine agent state
        let agentState: 'idle' | 'running' | 'starting' | 'done' = 'idle'
        if (activeItem) agentState = 'running'
        else if (isStarting) agentState = 'starting'
        else if (completedItems.length > 0) agentState = 'done'

        return (
          <div key={agent.key} className="bg-zinc-900 border border-zinc-800 rounded-xl overflow-hidden">
            {/* Agent header */}
            <div className="p-4 flex items-start justify-between gap-4">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-1">
                  <Icon size={18} className={`text-${agent.color}-400`} weight="duotone" />
                  <h3 className="font-medium text-sm">{agent.label}</h3>
                  {agentState === 'running' && (
                    <span className="flex items-center gap-1 text-xs text-emerald-400">
                      <CircleNotch size={10} className="animate-spin" /> running
                    </span>
                  )}
                  {agentState === 'starting' && (
                    <span className="flex items-center gap-1 text-xs text-amber-400">
                      <CircleNotch size={10} className="animate-spin" /> starting...
                    </span>
                  )}
                  {agentState === 'done' && !activeItem && (
                    <span className="flex items-center gap-1 text-xs text-zinc-500">
                      <CheckCircle size={10} /> {completedItems.length} tasks done
                    </span>
                  )}
                </div>
                <p className="text-xs text-zinc-500 leading-relaxed">{agent.description}</p>
              </div>

              <button
                onClick={() => handleRun(agent.key)}
                disabled={agentState === 'running' || agentState === 'starting'}
                className="shrink-0 inline-flex items-center gap-1.5 bg-zinc-800 hover:bg-zinc-700 disabled:opacity-40 disabled:cursor-not-allowed text-zinc-300 px-3 py-2 rounded-lg text-xs font-medium transition-colors"
              >
                {agentState === 'running' || agentState === 'starting' ? (
                  <CircleNotch size={12} className="animate-spin" />
                ) : (
                  <Lightning size={12} weight="fill" />
                )}
                {agentState === 'running' ? 'Running...' : agentState === 'starting' ? 'Starting...' : 'Run'}
              </button>
            </div>

            {/* Active work — what it's doing RIGHT NOW */}
            {activeItem && (
              <div className="px-4 pb-3">
                <div className="bg-emerald-500/[0.05] border border-emerald-500/10 rounded-lg p-3">
                  <div className="flex items-center gap-2 mb-1">
                    <CircleNotch size={12} className="text-emerald-400 animate-spin" />
                    <span className="text-xs font-medium text-emerald-400">{activeItem.category.replace(/_/g, ' ')}</span>
                    {activeItem.started_at && (
                      <span className="text-xs text-zinc-600 ml-auto tabular-nums">
                        started {timeAgo(activeItem.started_at)}
                      </span>
                    )}
                  </div>
                  <p className="text-sm text-zinc-300 leading-relaxed">
                    {activeItem.description.length > 200 ? activeItem.description.slice(0, 200) + '...' : activeItem.description}
                  </p>
                </div>
              </div>
            )}

            {/* Queue + stats bar */}
            <div className="px-4 pb-3 flex items-center gap-4 text-xs text-zinc-500">
              {pendingItems.length > 0 && (
                <span className="flex items-center gap-1">
                  <Clock size={10} /> {pendingItems.length} queued
                  <Tooltip text={`Next up: ${pendingItems[0]?.description.slice(0, 80)}...`} />
                </span>
              )}
              {completedItems.length > 0 && (
                <span className="flex items-center gap-1 text-emerald-500/70">
                  <CheckCircle size={10} /> {completedItems.length} done
                </span>
              )}
              {failedItems.length > 0 && (
                <span className="flex items-center gap-1 text-red-400/70">
                  <Warning size={10} /> {failedItems.length} failed
                  <Tooltip text={`Last failure: ${failedItems[0]?.result_summary?.slice(0, 100) || 'Unknown error'}. Failed items will be retried on next run.`} />
                </span>
              )}
              {lastSession && (
                <span className="ml-auto text-zinc-600">
                  last ran {lastSession.completed_at ? timeAgo(lastSession.completed_at) : 'running'}
                </span>
              )}
            </div>

            {/* Failed items detail (collapsed, show last one) */}
            {failedItems.length > 0 && !activeItem && (
              <div className="px-4 pb-3">
                <div className="bg-red-500/[0.05] border border-red-500/10 rounded-lg p-3">
                  <div className="flex items-center gap-2 mb-1">
                    <XCircle size={12} className="text-red-400" />
                    <span className="text-xs font-medium text-red-400">Last failure</span>
                  </div>
                  <p className="text-xs text-zinc-400">
                    {failedItems[0]?.result_summary?.slice(0, 150) || 'Unknown error'}
                  </p>
                  <button
                    onClick={() => handleRun(agent.key)}
                    className="mt-2 inline-flex items-center gap-1 text-xs text-zinc-400 hover:text-emerald-400 transition-colors"
                  >
                    <ArrowClockwise size={10} /> Retry
                  </button>
                </div>
              </div>
            )}
          </div>
        )
      })}

      {/* Session history */}
      {sessions.length > 0 && (
        <div>
          <h3 className="text-xs font-medium text-zinc-500 uppercase tracking-wider mb-2">Session History</h3>
          <div className="bg-zinc-900 border border-zinc-800 rounded-xl divide-y divide-zinc-800/60">
            {sessions.slice(0, 8).map((s) => {
              const meta = AGENTS.find(a => a.key === s.agent_type)
              const Icon = meta?.icon ?? Brain
              return (
                <div key={s.id} className="px-4 py-2.5 flex items-center gap-3 text-sm">
                  <Icon size={14} className={`text-${meta?.color ?? 'zinc'}-400`} weight="duotone" />
                  <span className="text-zinc-300 text-xs">{meta?.label ?? s.agent_type}</span>
                  <span className="text-zinc-600 text-xs">
                    {s.items_completed > 0 && `${s.items_completed} done`}
                    {s.items_failed > 0 && `, ${s.items_failed} failed`}
                    {s.knowledge_added > 0 && ` — +${s.knowledge_added} findings`}
                  </span>
                  <span className="text-zinc-600 text-xs ml-auto">
                    {s.completed_at ? timeAgo(s.completed_at) : 'running...'}
                  </span>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
