import { useSessions } from '../api/hooks'

// status → badge tone (mirrors the draft badges; falls back to neutral)
const STATUS_TONE: Record<string, string> = {
  ready_for_review: 'bg-sky-100 text-sky-800',
  applied: 'bg-green-600 text-white',
  awaiting_input: 'bg-amber-100 text-amber-800',
  failed: 'bg-red-100 text-red-700',
}

function fmt(ts: string | null): string {
  if (!ts) return '—'
  const d = new Date(ts)
  return isNaN(d.getTime()) ? ts : d.toLocaleString()
}

/** Session history (E6/E7): past runs, newest first; click "Open" to resume/review/apply. */
export function HistoryView({ onOpen }: { onOpen: (sessionId: string) => void }) {
  const { data, isLoading, isError } = useSessions({ limit: 50 })
  const sessions = data?.sessions ?? []

  return (
    <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
      <h2 className="text-sm font-semibold text-slate-700">Session history</h2>
      {isLoading && <p className="mt-3 text-sm text-slate-500">Loading…</p>}
      {isError && <p className="mt-3 text-sm text-red-600">Couldn't load history.</p>}
      {!isLoading && !isError && sessions.length === 0 && (
        <p className="mt-3 text-sm text-slate-500">No runs yet — document a table to get started.</p>
      )}
      <ul className="mt-2 divide-y divide-slate-100">
        {sessions.map((s) => (
          <li key={s.session_id} className="flex items-center justify-between gap-3 py-3">
            <div className="min-w-0">
              <p className="truncate font-mono text-sm text-slate-700">{s.target}</p>
              <p className="text-xs text-slate-400">
                {s.n_columns} cols · {s.n_applied} applied · {s.created_by} · {fmt(s.updated_at)}
              </p>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              <span
                className={`rounded px-2 py-0.5 text-xs font-medium ${
                  STATUS_TONE[s.status] ?? 'bg-slate-100 text-slate-600'
                }`}
              >
                {s.status.replace(/_/g, ' ')}
              </span>
              <button
                type="button"
                onClick={() => onOpen(s.session_id)}
                className="rounded border border-slate-300 px-2.5 py-1 text-xs font-medium text-slate-600 hover:bg-slate-50"
              >
                Open
              </button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}
