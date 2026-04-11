'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { api } from '@/lib/api'
import type { UatRunSummary, UatRunStatus } from '@/lib/types'

const STATUS_STYLES: Record<UatRunStatus, string> = {
  pending:   'bg-zinc-800 text-zinc-400 border-zinc-700',
  running:   'bg-blue-950 text-blue-300 border-blue-900',
  completed: 'bg-emerald-950 text-emerald-300 border-emerald-900',
  failed:    'bg-red-950 text-red-300 border-red-900',
}

export default function RunsListPage({ params }: { params: { id: string } }) {
  const projectId = parseInt(params.id, 10)
  const [runs, setRuns] = useState<UatRunSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api
      .listUatRuns(projectId)
      .then(setRuns)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [projectId])

  return (
    <div>
      <Link
        href={`/projects/${projectId}`}
        className="text-zinc-500 hover:text-zinc-300 text-sm mb-4 inline-block"
      >
        ← Back to project
      </Link>
      <div className="flex items-start justify-between mb-6">
        <div>
          <h1 className="text-3xl font-bold">UAT Runs</h1>
          <p className="text-zinc-400 mt-1">
            Each run installs the APK, navigates autonomously through every Figma frame, and produces a comparison report.
          </p>
        </div>
        <Link
          href={`/projects/${projectId}/runs/new`}
          className="bg-indigo-600 hover:bg-indigo-500 text-white px-4 py-2 rounded-md font-medium transition"
        >
          + Start UAT run
        </Link>
      </div>

      {loading && <p className="text-zinc-500">Loading…</p>}
      {error && <p className="text-red-400">Error: {error}</p>}

      {!loading && !error && runs.length === 0 && (
        <div className="border border-dashed border-zinc-700 rounded-lg p-12 text-center">
          <p className="text-zinc-400 mb-4">No UAT runs yet.</p>
          <Link
            href={`/projects/${projectId}/runs/new`}
            className="text-indigo-400 hover:text-indigo-300 font-medium"
          >
            Start your first run →
          </Link>
        </div>
      )}

      {runs.length > 0 && (
        <div className="space-y-2">
          {runs.map((r) => {
            const score = r.overall_match_score !== null ? `${(r.overall_match_score * 100).toFixed(0)}%` : '—'
            return (
              <Link
                key={r.id}
                href={`/projects/${projectId}/runs/${r.id}`}
                className="border border-zinc-800 bg-zinc-900/50 hover:border-zinc-700 hover:bg-zinc-900 rounded p-4 flex items-center justify-between transition gap-3"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-3">
                    <span className="font-medium">Run #{r.id}</span>
                    {r.apk_version && <span className="text-xs text-zinc-500 font-mono">v{r.apk_version}</span>}
                    <span className={`text-xs px-2 py-0.5 rounded border ${STATUS_STYLES[r.status]}`}>
                      {r.status}
                    </span>
                  </div>
                  {r.feature_description && (
                    <p className="text-xs text-zinc-500 truncate mt-1 italic">"{r.feature_description}"</p>
                  )}
                  <div className="flex gap-4 mt-2 text-xs text-zinc-600">
                    <span>✅ {r.matched}</span>
                    <span>⚠️ {r.mismatched}</span>
                    <span>❌ {r.unreachable}</span>
                    <span className="text-zinc-500">of {r.total_frames}</span>
                  </div>
                </div>
                <div className="text-right shrink-0">
                  <div className="text-2xl font-bold text-zinc-300">{score}</div>
                  <div className="text-xs text-zinc-600">
                    {new Date(r.started_at).toLocaleDateString()}
                  </div>
                </div>
              </Link>
            )
          })}
        </div>
      )}
    </div>
  )
}
