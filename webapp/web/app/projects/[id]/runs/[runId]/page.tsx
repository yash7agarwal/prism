'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/api'
import type { UatRun } from '@/lib/types'
import { FrameComparisonCard } from '@/components/FrameComparisonCard'

export default function UatRunDetailPage({
  params,
}: {
  params: { id: string; runId: string }
}) {
  const projectId = parseInt(params.id, 10)
  const runId = parseInt(params.runId, 10)
  const [run, setRun] = useState<UatRun | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .getUatRun(runId)
      .then(setRun)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))

    // Poll while running
    const interval = setInterval(async () => {
      try {
        const r = await api.getUatRun(runId)
        setRun(r)
        if (r.status === 'completed' || r.status === 'failed') {
          clearInterval(interval)
        }
      } catch {
        /* swallow — next tick */
      }
    }, 3000)
    return () => clearInterval(interval)
  }, [runId])

  if (loading) return <p className="text-zinc-500">Loading…</p>
  if (error) return <p className="text-red-400">Error: {error}</p>
  if (!run) return <p className="text-zinc-500">Run not found</p>

  const scorePct =
    run.overall_match_score !== null ? `${(run.overall_match_score * 100).toFixed(0)}%` : '—'

  return (
    <div>
      <Link
        href={`/projects/${projectId}/runs`}
        className="text-zinc-500 hover:text-zinc-300 text-sm mb-4 inline-block"
      >
        ← All runs
      </Link>

      {/* Header */}
      <div className="border border-zinc-800 bg-zinc-900/30 rounded-lg p-5 mb-6">
        <div className="flex items-start justify-between gap-6">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-3 mb-2 flex-wrap">
              <h1 className="text-2xl font-bold">Run #{run.id}</h1>
              <StatusBadge status={run.status} />
              {run.apk_version && (
                <span className="text-xs text-zinc-500 font-mono bg-zinc-900 px-2 py-0.5 rounded">
                  v{run.apk_version}
                </span>
              )}
            </div>
            {run.feature_description && (
              <p className="text-zinc-400 italic mb-1">"{run.feature_description}"</p>
            )}
            {run.figma_file_id && (
              <p className="text-xs text-zinc-600 font-mono">Figma: {run.figma_file_id}</p>
            )}
            <p className="text-xs text-zinc-600 mt-1">
              Started {new Date(run.started_at).toLocaleString()}
              {run.completed_at && ` · Completed ${new Date(run.completed_at).toLocaleString()}`}
            </p>
          </div>
          <div className="text-center shrink-0">
            <div className="text-5xl font-bold text-zinc-200">{scorePct}</div>
            <div className="text-xs text-zinc-500 uppercase tracking-wider mt-1">match</div>
          </div>
        </div>

        {/* Counts */}
        <div className="flex gap-6 mt-5 pt-5 border-t border-zinc-800">
          <Stat label="Total" value={run.total_frames} />
          <Stat label="Matched" value={run.matched} emoji="✅" />
          <Stat label="Differs" value={run.mismatched} emoji="⚠️" />
          <Stat label="Unreachable" value={run.unreachable} emoji="❌" />
          {run.report_md_path && (
            <a
              href={api.uatReportMdUrl(run.id)}
              target="_blank"
              rel="noreferrer"
              className="ml-auto text-sm text-indigo-400 hover:text-indigo-300 self-end"
            >
              Download report.md →
            </a>
          )}
        </div>
      </div>

      {/* Error state */}
      {run.status === 'failed' && run.error && (
        <div className="border border-red-900 bg-red-950/30 text-red-200 p-4 rounded-lg mb-6">
          <p className="font-semibold mb-2">Run failed</p>
          <pre className="text-xs whitespace-pre-wrap font-mono">{run.error.slice(0, 800)}</pre>
        </div>
      )}

      {run.status === 'running' && (
        <div className="border border-blue-900 bg-blue-950/20 text-blue-200 p-4 rounded-lg mb-6">
          ⏳ Run in progress. Page auto-refreshes every 3 seconds.
        </div>
      )}

      {/* Per-frame cards */}
      {run.frame_results && run.frame_results.length > 0 && (
        <div className="space-y-4">
          <h2 className="text-lg font-semibold">Per-frame comparison</h2>
          {run.frame_results.map((fr) => (
            <FrameComparisonCard key={fr.id} runId={run.id} frame={fr} />
          ))}
        </div>
      )}
    </div>
  )
}

function StatusBadge({ status }: { status: UatRun['status'] }) {
  const styles: Record<string, string> = {
    pending: 'bg-zinc-800 text-zinc-400 border-zinc-700',
    running: 'bg-blue-950 text-blue-300 border-blue-900',
    completed: 'bg-emerald-950 text-emerald-300 border-emerald-900',
    failed: 'bg-red-950 text-red-300 border-red-900',
  }
  return (
    <span className={`text-xs px-2 py-0.5 rounded border ${styles[status] || styles.pending}`}>
      {status}
    </span>
  )
}

function Stat({ label, value, emoji }: { label: string; value: number; emoji?: string }) {
  return (
    <div className="text-center">
      <div className="text-2xl font-semibold">
        {emoji && <span className="mr-1">{emoji}</span>}
        {value}
      </div>
      <div className="text-xs text-zinc-500 uppercase tracking-wide">{label}</div>
    </div>
  )
}
