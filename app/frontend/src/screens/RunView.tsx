import type { SessionStatus, SessionView } from '../api/types'

// "Watch it think" (U9 §4): narrate the real orchestrator phases as the session polls.
const PHASE_LABEL: Partial<Record<SessionStatus, string>> = {
  created: 'Starting…',
  profiling: 'Profiling the table — distributions, cardinality, key-likeness…',
  gathering_context: 'Reading lineage & recent query history…',
  reasoning: 'Drafting AI-ready comments…',
  applying: 'Applying comments to Unity Catalog…',
}

export function RunView({ session }: { session: SessionView }) {
  const label = PHASE_LABEL[session.status] ?? 'Working…'
  const cols = session.column_drafts
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="flex items-center gap-3">
        <span className="h-2.5 w-2.5 animate-pulse rounded-full bg-confidence-mid" aria-hidden />
        <p className="text-sm font-medium text-slate-700">{label}</p>
      </div>
      <p className="mt-1 font-mono text-xs text-slate-400">{session.target}</p>

      {cols.length > 0 && (
        <>
          <p className="mt-4 text-xs uppercase tracking-wide text-slate-400">
            Columns ({cols.length})
          </p>
          <ul className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3">
            {cols.map((c) => (
              <li
                key={c.column_name}
                className="truncate rounded border border-slate-100 bg-slate-50 px-2 py-1 font-mono text-xs text-slate-600"
                title={c.column_name}
              >
                {c.column_name}
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}
