import { useState } from 'react'
import { useLibrary, useReviveLibrary, useSunsetLibrary } from '../api/hooks'
import { PillRow } from '../components/Pill'
import { cn } from '../lib/cn'
import type { LibraryStatus } from '../api/types'

const STATUS_BADGE: Record<LibraryStatus, string> = {
  approved: 'bg-sky-100 text-sky-800',
  applied: 'bg-green-600 text-white',
  sunset: 'bg-slate-200 text-slate-500',
}

/** Comment library (E6/D52): a governed definition store — approved → applied → sunset.
 * Approving/applying a draft seeds it; reuse-on-generation pulls from it; sunset retires an
 * entry (soft, revivable). Sunset entries hidden by default. */
export function LibraryView() {
  const [showSunset, setShowSunset] = useState(false)
  const { data, isLoading, isError } = useLibrary({ limit: 100, include_sunset: showSunset })
  const sunset = useSunsetLibrary()
  const revive = useReviveLibrary()
  const entries = data?.entries ?? []

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-slate-700">Comment library</h2>
        <label className="flex items-center gap-1.5 text-xs text-slate-500">
          <input
            type="checkbox"
            checked={showSunset}
            onChange={(e) => setShowSunset(e.target.checked)}
            className="rounded border-slate-300"
          />
          Show sunset
        </label>
      </div>
      {isLoading && <p className="mt-3 text-sm text-slate-500">Loading…</p>}
      {isError && <p className="mt-3 text-sm text-red-600">Couldn't load the library.</p>}
      {!isLoading && !isError && entries.length === 0 && (
        <p className="mt-3 text-sm text-slate-500">Empty — approved &amp; applied comments are added here for reuse.</p>
      )}
      <ul className="mt-2 space-y-2">
        {entries.map((e) => {
          const isSunset = e.status === 'sunset'
          return (
            <li
              key={e.id}
              className={cn('rounded-lg border border-slate-100 bg-slate-50/50 p-3', isSunset && 'opacity-60')}
            >
              <div className="flex items-center justify-between gap-2">
                <span className={cn('truncate font-mono text-sm text-slate-700', isSunset && 'line-through')}>
                  {e.match_key}
                </span>
                <div className="flex shrink-0 items-center gap-2">
                  <span className={`rounded px-2 py-0.5 text-[11px] font-medium ${STATUS_BADGE[e.status]}`}>
                    {e.status}
                  </span>
                  <span className="text-xs text-slate-400">
                    {e.scope} · used {e.usage_count}×
                  </span>
                </div>
              </div>
              <p className="mt-1 text-sm text-slate-600">{e.canonical_comment}</p>
              <div className="mt-1.5 flex items-center justify-between gap-2">
                <PillRow tags={e.tags} />
                {isSunset ? (
                  <button
                    type="button"
                    onClick={() => revive.mutate(e.id)}
                    disabled={revive.isPending}
                    className="shrink-0 rounded border border-sky-300 px-2 py-0.5 text-xs text-sky-700 hover:bg-sky-50 disabled:opacity-40"
                  >
                    Revive
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={() => sunset.mutate(e.id)}
                    disabled={sunset.isPending}
                    className="shrink-0 rounded border border-slate-300 px-2 py-0.5 text-xs text-slate-500 hover:bg-slate-100 disabled:opacity-40"
                    title="Retire this definition — excluded from reuse (revivable)"
                  >
                    Sunset
                  </button>
                )}
              </div>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
