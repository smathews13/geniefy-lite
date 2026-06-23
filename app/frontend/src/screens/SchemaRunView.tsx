import { useState } from 'react'
import {
  useApproveTableInRun,
  useCancelSchemaRun,
  useSchemaRun,
  useSchemaRuns,
  useStartSchemaRun,
} from '../api/hooks'
import { cn } from '../lib/cn'
import type { SchemaRun, SchemaRunStatus, SessionSummary } from '../api/types'

/** One table row in a schema run: open it, see its overall confidence, or bulk-approve its
 * high-confidence drafts in place (LLD-amend-007 §4). Bulk-approve never writes to UC (approve != apply). */
function SchemaTableRow({
  s,
  runId,
  onOpen,
}: {
  s: SessionSummary
  runId: string
  onOpen: (sessionId: string) => void
}) {
  const approve = useApproveTableInRun(s.session_id, runId)
  const cs = s.confidence_summary
  const n = cs?.approvable ?? 0
  return (
    <li className="flex items-center gap-2 rounded-lg border border-slate-100 p-2.5 transition hover:bg-slate-50">
      <button
        type="button"
        onClick={() => onOpen(s.session_id)}
        className="flex min-w-0 flex-1 items-center justify-between gap-2 text-left"
        title="Open this table to review / answer questions / apply"
      >
        <span className="truncate font-mono text-sm text-slate-700">{s.target}</span>
        <span className="flex shrink-0 items-center gap-2 text-xs text-slate-500">
          <span
            className="font-medium text-slate-600"
            title={
              cs
                ? `${cs.review_ready} review-ready · ${cs.needs_input} need input · ${cs.low} low`
                : 'No confidence yet (not reviewable)'
            }
          >
            {cs?.overall != null ? `${Math.round(cs.overall * 100)}% conf` : '— conf'}
          </span>
          <span>
            {s.status.replace(/_/g, ' ')}
            {s.n_applied ? ` · ${s.n_applied} applied` : ''}
          </span>
        </span>
      </button>
      <button
        type="button"
        onClick={() => approve.mutate()}
        disabled={!n || approve.isPending}
        className="shrink-0 rounded-md border border-confidence-high px-2 py-1 text-xs font-medium text-confidence-high transition hover:bg-emerald-50 disabled:cursor-not-allowed disabled:border-slate-200 disabled:text-slate-300"
        title={
          n
            ? "Approve this table's high-confidence drafts (does not write to Unity Catalog)"
            : 'No high-confidence drafts to approve'
        }
      >
        {approve.isPending ? 'Approving…' : `Approve ${n}`}
      </button>
    </li>
  )
}

const RUN_BADGE: Record<SchemaRunStatus, string> = {
  enumerating: 'bg-indigo-100 text-indigo-700',
  running: 'bg-indigo-100 text-indigo-700',
  completed: 'bg-green-100 text-green-800',
  completed_with_errors: 'bg-amber-100 text-amber-800',
  failed: 'bg-red-100 text-red-700',
  cancelled: 'bg-slate-200 text-slate-500',
}
const isActive = (s: SchemaRunStatus) => s === 'enumerating' || s === 'running'
const INPUT = 'rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-confidence-high focus:outline-none focus:ring-1 focus:ring-confidence-high'

/** Hands-off schema documentation (D51): point at a schema → a Job documents every table; come
 * back to review/clarify/apply. Nothing is written to UC until a human approves (Q7). */
export function SchemaRunView({ onOpen }: { onOpen: (sessionId: string) => void }) {
  const [selected, setSelected] = useState<string | null>(null)
  return selected
    ? <SchemaRunDetail runId={selected} onBack={() => setSelected(null)} onOpen={onOpen} />
    : <SchemaRunList onSelect={setSelected} />
}

function RollupChips({ run }: { run: SchemaRun }) {
  const c = run.counts || {}
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-xs">
      {run.total_tables != null && <span className="text-slate-500">{run.total_tables} tables</span>}
      <span className="rounded-full bg-green-50 px-2 py-0.5 text-green-700 ring-1 ring-green-100">{c.ready ?? 0} ready</span>
      <span className="rounded-full bg-amber-50 px-2 py-0.5 text-amber-800 ring-1 ring-amber-100">{c.needs_input ?? 0} need input</span>
      <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-emerald-700 ring-1 ring-emerald-100">{c.applied ?? 0} applied</span>
      {(c.error ?? 0) > 0 && <span className="rounded-full bg-red-50 px-2 py-0.5 text-red-700 ring-1 ring-red-100">{c.error} error</span>}
      {(c.skipped ?? 0) > 0 && <span className="rounded-full bg-slate-100 px-2 py-0.5 text-slate-500">{c.skipped} skipped</span>}
    </div>
  )
}

function SchemaRunList({ onSelect }: { onSelect: (id: string) => void }) {
  const [catalog, setCatalog] = useState('')
  const [schema, setSchema] = useState('')
  const [skipDocumented, setSkipDocumented] = useState(true)
  const start = useStartSchemaRun()
  const { data, isLoading } = useSchemaRuns({ limit: 50 })
  const runs = data?.schema_runs ?? []
  const valid = !!catalog.trim() && !!schema.trim()
  const go = () =>
    start.mutate(
      { catalog: catalog.trim(), schema: schema.trim(), filters: { skip_documented: skipDocumented } },
      { onSuccess: (r) => onSelect(r.schema_run_id) },
    )

  return (
    <div className="space-y-6">
      <div className="rounded-xl border border-indigo-200 bg-gradient-to-br from-indigo-50/60 to-white p-6 shadow-sm">
        <h2 className="text-sm font-semibold text-slate-700">Document a whole schema (hands-off)</h2>
        <p className="mt-1 text-xs text-slate-500">
          Point at a schema; a job documents every table in the background. Come back any time to
          review, answer the agent's questions, and apply — nothing is written to Unity Catalog until you approve it.
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <input id="schema-run-catalog" name="catalog" value={catalog} onChange={(e) => setCatalog(e.target.value)} placeholder="catalog" className={INPUT} />
          <span className="text-slate-400">.</span>
          <input id="schema-run-schema" name="schema" value={schema} onChange={(e) => setSchema(e.target.value)} placeholder="schema" className={INPUT} />
          <label className="flex items-center gap-1.5 text-xs text-slate-600">
            <input id="schema-run-skip-documented" name="skip_documented" type="checkbox" checked={skipDocumented} onChange={(e) => setSkipDocumented(e.target.checked)} className="rounded border-slate-300" />
            skip already-documented
          </label>
          <button
            type="button"
            onClick={go}
            disabled={!valid || start.isPending}
            className={cn('rounded-md px-4 py-2 text-sm font-medium text-white transition',
              valid && !start.isPending ? 'bg-slate-900 hover:bg-slate-700' : 'cursor-not-allowed bg-slate-300')}
          >
            {start.isPending ? 'Starting…' : 'Document schema'}
          </button>
        </div>
        {start.isError && <p className="mt-2 text-xs text-red-600">Couldn't start the schema run — check the catalog/schema and your access.</p>}
      </div>

      <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <h2 className="text-sm font-semibold text-slate-700">Schema runs</h2>
        {isLoading && <p className="mt-3 text-sm text-slate-500">Loading…</p>}
        {!isLoading && runs.length === 0 && <p className="mt-3 text-sm text-slate-500">No schema runs yet — start one above.</p>}
        <ul className="mt-2 space-y-2">
          {runs.map((r) => (
            <li key={r.id}>
              <button
                type="button"
                onClick={() => onSelect(r.id)}
                className="w-full rounded-lg border border-slate-100 bg-slate-50/50 p-3 text-left transition hover:bg-slate-50"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono text-sm text-slate-700">{r.catalog}.{r.schema}</span>
                  <span className={cn('rounded px-2 py-0.5 text-xs font-medium', RUN_BADGE[r.status])}>{r.status.replace(/_/g, ' ')}</span>
                </div>
                <div className="mt-1.5"><RollupChips run={r} /></div>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}

function SchemaRunDetail({ runId, onBack, onOpen }: {
  runId: string; onBack: () => void; onOpen: (sessionId: string) => void
}) {
  const { data: run, isLoading } = useSchemaRun(runId)
  const cancel = useCancelSchemaRun()

  if (isLoading || !run) return <p className="text-sm text-slate-500">Loading schema run…</p>
  const sessions = run.sessions ?? []

  return (
    <div className="space-y-4">
      <button type="button" onClick={onBack} className="text-sm text-slate-500 transition hover:text-slate-700">← All schema runs</button>

      <div className="rounded-xl border border-indigo-200 bg-gradient-to-br from-indigo-50 to-white p-5 shadow-sm">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-[10px] font-semibold uppercase tracking-wider text-indigo-500">Hands-off schema run</p>
            <p className="truncate font-mono text-base font-semibold text-slate-800">{run.catalog}.{run.schema}</p>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <span className={cn('rounded px-2 py-0.5 text-xs font-medium', RUN_BADGE[run.status])}>{run.status.replace(/_/g, ' ')}</span>
            {isActive(run.status) && (
              <button
                type="button"
                onClick={() => cancel.mutate(runId)}
                disabled={cancel.isPending || cancel.isSuccess}
                className="rounded border border-slate-300 px-2 py-0.5 text-xs text-slate-600 hover:bg-slate-100 disabled:opacity-40"
              >
                Cancel
              </button>
            )}
          </div>
        </div>
        <div className="mt-2"><RollupChips run={run} /></div>
        {isActive(run.status) && (
          <p className="mt-2 flex items-center gap-1.5 text-xs text-indigo-600">
            <span className="animate-pulse">●</span> Generating in the background — this view updates automatically.
          </p>
        )}
      </div>

      <div className="rounded-xl border border-slate-200 bg-white p-5 shadow-sm">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400">Tables ({sessions.length})</h3>
        {sessions.length === 0 && (
          <p className="mt-2 text-sm text-slate-500">
            {isActive(run.status) ? 'Enumerating + generating — tables will appear here as they finish.' : 'No tables documented in this run.'}
          </p>
        )}
        <ul className="mt-2 space-y-1.5">
          {sessions.map((s) => (
            <SchemaTableRow key={s.session_id} s={s} runId={runId} onOpen={onOpen} />
          ))}
        </ul>
      </div>
    </div>
  )
}
