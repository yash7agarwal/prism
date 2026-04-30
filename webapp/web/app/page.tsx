'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { Folder, Plus, ArrowRight, DotsThree, EyeSlash, Eye, Trash } from '@phosphor-icons/react'
import { api } from '@/lib/api'
import type { Project } from '@/lib/types'

export default function HomePage() {
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [includeHidden, setIncludeHidden] = useState(false)
  const [openMenuId, setOpenMenuId] = useState<number | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<Project | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [flash, setFlash] = useState<{ msg: string; project?: Project; action?: 'hide' | 'delete' } | null>(null)

  const load = async (showHidden = includeHidden) => {
    setError(null)
    try {
      const data = await api.listProjects(showHidden)
      setProjects(data)
    } catch (e: any) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load(includeHidden) }, [includeHidden])

  // Close menu on outside click
  useEffect(() => {
    if (openMenuId === null) return
    const onDoc = () => setOpenMenuId(null)
    document.addEventListener('click', onDoc)
    return () => document.removeEventListener('click', onDoc)
  }, [openMenuId])

  const stop = (e: React.MouseEvent | React.SyntheticEvent) => {
    e.preventDefault()
    e.stopPropagation()
  }

  const handleHide = async (p: Project, e: React.MouseEvent) => {
    stop(e)
    setOpenMenuId(null)
    setBusyId(p.id)
    try {
      await api.hideProject(p.id)
      // Optimistic remove from current view (unless we're showing hidden).
      if (!includeHidden) setProjects(prev => prev.filter(x => x.id !== p.id))
      else setProjects(prev => prev.map(x => x.id === p.id ? { ...x, is_hidden: true } : x))
      setFlash({ msg: `Hidden “${p.name}”.`, project: p, action: 'hide' })
      setTimeout(() => setFlash(null), 8000)
    } catch (e: any) {
      setError(e.message || 'Failed to hide')
    } finally {
      setBusyId(null)
    }
  }

  const handleUnhide = async (p: Project, e: React.MouseEvent) => {
    stop(e)
    setOpenMenuId(null)
    setBusyId(p.id)
    try {
      await api.unhideProject(p.id)
      setProjects(prev => prev.map(x => x.id === p.id ? { ...x, is_hidden: false } : x))
      setFlash({ msg: `Restored “${p.name}”.` })
      setTimeout(() => setFlash(null), 4000)
    } catch (e: any) {
      setError(e.message || 'Failed to unhide')
    } finally {
      setBusyId(null)
    }
  }

  const handleConfirmDelete = async () => {
    if (!confirmDelete) return
    const p = confirmDelete
    setConfirmDelete(null)
    setBusyId(p.id)
    try {
      await api.deleteProject(p.id)
      setProjects(prev => prev.filter(x => x.id !== p.id))
      setFlash({ msg: `Deleted “${p.name}”. This cannot be undone.` })
      setTimeout(() => setFlash(null), 6000)
    } catch (e: any) {
      setError(e.message || 'Failed to delete')
    } finally {
      setBusyId(null)
    }
  }

  const handleUndoFlash = async () => {
    if (!flash?.project || flash.action !== 'hide') return
    const p = flash.project
    setFlash(null)
    setBusyId(p.id)
    try {
      await api.unhideProject(p.id)
      // If we were filtering hidden, reload to fetch the project back.
      if (!includeHidden) await load(false)
      else setProjects(prev => prev.map(x => x.id === p.id ? { ...x, is_hidden: false } : x))
    } catch (e: any) {
      setError(e.message || 'Failed to undo')
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Your products</h1>
          <p className="text-zinc-400 mt-1 text-sm">
            Each product tracks one competitive landscape — research, trends, impact analysis, and lens-based insights in one place.
          </p>
        </div>
        <Link
          href="/projects/new"
          className="inline-flex items-center gap-1.5 bg-emerald-600 hover:bg-emerald-500 text-white px-4 py-2 rounded-lg font-medium text-sm transition-colors duration-150 active:scale-[0.98] active:translate-y-[1px]"
        >
          <Plus size={16} weight="bold" />
          New product
        </Link>
      </div>

      {/* Show-hidden toggle */}
      <div className="flex items-center justify-end mb-4">
        <label className="inline-flex items-center gap-2 text-xs text-zinc-500 cursor-pointer hover:text-zinc-300 select-none">
          <input
            type="checkbox"
            checked={includeHidden}
            onChange={(e) => setIncludeHidden(e.target.checked)}
            className="accent-emerald-500"
          />
          Show hidden
        </label>
      </div>

      {/* Flash banner with undo when applicable */}
      {flash && (
        <div className="mb-4 px-4 py-2.5 rounded-lg bg-zinc-900 border border-zinc-700 flex items-center justify-between gap-4">
          <span className="text-sm text-zinc-300">{flash.msg}</span>
          {flash.action === 'hide' && (
            <button
              onClick={handleUndoFlash}
              className="text-xs text-emerald-400 hover:text-emerald-300 font-medium underline underline-offset-2"
            >
              Undo
            </button>
          )}
        </div>
      )}

      {loading && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {[0, 1].map((i) => (
            <div key={i} className="bg-zinc-900 border border-zinc-800 rounded-xl p-5">
              <div className="skeleton h-5 w-32 mb-3" />
              <div className="skeleton h-3 w-20 mb-3" />
              <div className="skeleton h-4 w-full mb-2" />
              <div className="skeleton h-3 w-24 mt-3" />
            </div>
          ))}
        </div>
      )}
      {error && (
        <div className="border border-red-500/20 bg-red-500/10 text-red-200 p-4 rounded-xl">
          Error: {error}
          <p className="text-xs mt-2 text-red-300">
            Make sure the backend is running:{' '}
            <code className="font-mono text-zinc-400">.venv/bin/python3 -m uvicorn webapp.api.main:app --reload --port 8000</code>
          </p>
        </div>
      )}

      {!loading && !error && projects.length === 0 && (
        <div className="border border-dashed border-zinc-800 rounded-xl p-12 text-center">
          <Folder size={32} className="text-zinc-600 mx-auto mb-3" />
          <p className="text-zinc-400 text-sm mb-4">
            {includeHidden ? 'No products (including hidden).' : 'No products yet — or all are hidden.'}
          </p>
          <Link
            href="/projects/new"
            className="text-emerald-400 hover:text-emerald-300 font-medium text-sm"
          >
            Add your first product
          </Link>
        </div>
      )}

      {projects.length > 0 && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {projects.map((p, index) => {
            const menuOpen = openMenuId === p.id
            return (
              <Link
                key={p.id}
                href={`/projects/${p.id}`}
                className={`relative bg-zinc-900 border rounded-xl p-5 transition-all duration-200 animate-fade-in-up card-glow group ${
                  p.is_hidden
                    ? 'border-zinc-800 opacity-60 hover:opacity-90 hover:border-zinc-700'
                    : 'border-zinc-800 hover:border-zinc-700'
                }`}
                style={{ animationDelay: `${index * 80}ms` } as React.CSSProperties}
              >
                <div className="flex items-start justify-between mb-2 gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 flex-wrap">
                      <h3 className="font-medium text-base group-hover:text-emerald-400 transition-colors truncate">{p.name}</h3>
                      {p.is_hidden && (
                        <span className="inline-flex items-center gap-1 text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded text-zinc-500 bg-zinc-800/60 border border-zinc-700">
                          <EyeSlash size={10} /> hidden
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="flex items-center gap-1">
                    {/* Three-dot menu */}
                    <div className="relative" onClick={stop}>
                      <button
                        onClick={(e) => { stop(e); setOpenMenuId(menuOpen ? null : p.id) }}
                        disabled={busyId === p.id}
                        className="p-1 rounded hover:bg-zinc-800 text-zinc-500 hover:text-zinc-300 disabled:opacity-50"
                        aria-label="Project menu"
                      >
                        <DotsThree size={18} weight="bold" />
                      </button>
                      {menuOpen && (
                        <div
                          className="absolute right-0 top-full mt-1 z-20 w-48 bg-zinc-900 border border-zinc-700 rounded-lg shadow-xl py-1"
                          onClick={stop}
                        >
                          {p.is_hidden ? (
                            <button
                              onClick={(e) => handleUnhide(p, e)}
                              className="w-full text-left px-3 py-2 text-sm text-zinc-200 hover:bg-zinc-800 flex items-center gap-2"
                            >
                              <Eye size={14} /> Restore
                            </button>
                          ) : (
                            <button
                              onClick={(e) => handleHide(p, e)}
                              className="w-full text-left px-3 py-2 text-sm text-zinc-200 hover:bg-zinc-800 flex items-center gap-2"
                            >
                              <EyeSlash size={14} /> Hide from list
                            </button>
                          )}
                          <div className="my-1 border-t border-zinc-800" />
                          <button
                            onClick={(e) => { stop(e); setOpenMenuId(null); setConfirmDelete(p) }}
                            className="w-full text-left px-3 py-2 text-sm text-red-400 hover:bg-red-500/10 flex items-center gap-2"
                          >
                            <Trash size={14} /> Delete permanently…
                          </button>
                        </div>
                      )}
                    </div>
                    <ArrowRight size={16} className="text-zinc-700 group-hover:text-emerald-400 transition-colors" />
                  </div>
                </div>
                {p.app_package && (
                  <p className="text-xs text-zinc-600 font-mono mb-2">{p.app_package}</p>
                )}
                {p.description && (
                  <p className="text-sm text-zinc-400 line-clamp-2 mb-3">{p.description}</p>
                )}
                <div className="flex items-center justify-between pt-3 border-t border-zinc-800/50">
                  <p className="text-xs text-zinc-600">
                    Created {new Date(p.created_at).toLocaleDateString()}
                  </p>
                  <div className="flex items-center gap-1">
                    <span className={`w-1.5 h-1.5 rounded-full ${p.is_hidden ? 'bg-zinc-600' : 'bg-emerald-500/50'}`} />
                    <span className="text-xs text-zinc-600">{p.is_hidden ? 'Hidden' : 'Active'}</span>
                  </div>
                </div>
              </Link>
            )
          })}
        </div>
      )}

      {/* Permanent-delete confirmation modal */}
      {confirmDelete && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4"
          onClick={() => setConfirmDelete(null)}
        >
          <div
            className="bg-zinc-900 border border-zinc-700 rounded-xl p-6 max-w-md w-full"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="text-lg font-semibold text-zinc-100 mb-2">Delete “{confirmDelete.name}” permanently?</h2>
            <p className="text-sm text-zinc-400 mb-4 leading-relaxed">
              This removes the project and all its data: competitors, observations, work items,
              uploaded reports, and synthesized profiles. <span className="text-red-400 font-medium">It cannot be undone.</span>
            </p>
            <p className="text-xs text-zinc-500 mb-5">
              If you only want to declutter the list, choose <span className="text-emerald-400">Hide from list</span> instead — it's reversible.
            </p>
            <div className="flex items-center justify-end gap-2">
              <button
                onClick={() => setConfirmDelete(null)}
                className="px-4 py-2 text-sm text-zinc-300 hover:text-zinc-100 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleConfirmDelete}
                className="px-4 py-2 text-sm font-medium bg-red-600 hover:bg-red-500 text-white rounded-lg transition-colors"
              >
                Delete permanently
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
