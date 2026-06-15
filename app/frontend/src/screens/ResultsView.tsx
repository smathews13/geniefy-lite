import type { SessionView } from '../api/types'
import { ConfidenceChip } from '../viz'

// Read-only drafts view (U59). The interactive review (diff/edit/approve), the questions
// panel, explainability, and apply land in U60+.
export function ResultsView({ session }: { session: SessionView }) {
  const td = session.table_draft
  return (
    <div className="space-y-4">
      {td && (
        <section className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-slate-800">Table comment</h2>
            <ConfidenceChip score={td.confidence} />
          </div>
          <p className="mt-2 text-sm text-slate-700">{td.proposed_comment ?? '—'}</p>
        </section>
      )}

      <section className="rounded-xl border border-slate-200 bg-white shadow-sm">
        <h2 className="border-b border-slate-100 px-5 py-3 text-sm font-semibold text-slate-800">
          Columns ({session.column_drafts.length})
        </h2>
        <ul className="divide-y divide-slate-100">
          {session.column_drafts.map((c) => (
            <li key={c.column_name} className="flex items-start justify-between gap-4 px-5 py-3">
              <div className="min-w-0">
                <p className="font-mono text-xs text-slate-500">
                  {c.column_name}
                  {c.data_type ? ` · ${c.data_type}` : ''}
                </p>
                <p className="text-sm text-slate-700">{c.proposed_comment ?? '—'}</p>
              </div>
              <ConfidenceChip score={c.confidence} />
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}
