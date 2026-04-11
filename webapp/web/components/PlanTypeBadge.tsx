'use client'

import type { PlanType } from '@/lib/types'

const LABELS: Record<PlanType, { emoji: string; label: string; classes: string }> = {
  feature_flow: {
    emoji: '📋',
    label: 'Feature flow',
    classes: 'bg-zinc-800 text-zinc-300 border-zinc-700',
  },
  design_fidelity: {
    emoji: '🎨',
    label: 'Design fidelity',
    classes: 'bg-purple-950 text-purple-300 border-purple-900',
  },
  functional_flow: {
    emoji: '🔀',
    label: 'Functional flow',
    classes: 'bg-blue-950 text-blue-300 border-blue-900',
  },
  deeplink_utility: {
    emoji: '🔗',
    label: 'Deeplink / utility',
    classes: 'bg-amber-950 text-amber-300 border-amber-900',
  },
  edge_cases: {
    emoji: '⚠️',
    label: 'Edge cases',
    classes: 'bg-red-950 text-red-300 border-red-900',
  },
}

export function PlanTypeBadge({ type, size = 'sm' }: { type: PlanType; size?: 'sm' | 'md' }) {
  const spec = LABELS[type] || LABELS.feature_flow
  const sizing = size === 'md' ? 'text-sm px-2.5 py-1' : 'text-xs px-2 py-0.5'
  return (
    <span
      className={`inline-flex items-center gap-1 rounded border ${spec.classes} ${sizing} whitespace-nowrap`}
      title={spec.label}
    >
      <span>{spec.emoji}</span>
      <span className="font-medium">{spec.label}</span>
    </span>
  )
}
