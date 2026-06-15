import { useState } from 'react'
import { cn } from './lib/cn'
import { useRun, useSession } from './api/hooks'
import type { SessionStatus } from './api/types'
import { RunView } from './screens/RunView'
import { ReviewView } from './screens/ReviewView'
import { HistoryView } from './screens/HistoryView'
import { LibraryView } from './screens/LibraryView'
import { SchemaRunView } from './screens/SchemaRunView'
import { HowItWorks } from './screens/HowItWorks'
import { UserCard } from './screens/UserCard'

const IN_FLIGHT: SessionStatus[] = ['created', 'profiling', 'gathering_context', 'reasoning', 'applying']
const DONE: SessionStatus[] = ['awaiting_input', 'ready_for_review', 'applied']
const TABLE_RE = /^[^.\s]+\.[^.\s]+\.[^.\s]+$/

type View = 'run' | 'schema' | 'history' | 'library'
const NAV: { id: View; label: string; hint: string }[] = [
  { id: 'run', label: 'Table', hint: 'Document a single Unity Catalog table' },
  { id: 'schema', label: 'Schema', hint: 'Hands-off: document every table in a schema' },
  { id: 'history', label: 'History', hint: 'Past runs — reopen, resume, or review' },
  { id: 'library', label: 'Library', hint: 'Approved comments reused across tables' },
]

export default function App() {
  const [table, setTable] = useState('')
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [view, setView] = useState<View>('run')
  const run = useRun()
  const { data: session, isLoading } = useSession(sessionId)
  // once a session has drafts, keep showing the review screen even while a regenerate is in-flight
  // (U95) — only the targeted cards gray out; we don't flip the whole view back to "watch it think".
  const hasDrafts = !!session && (!!session.table_draft || session.column_drafts.length > 0)

  const valid = TABLE_RE.test(table.trim())
  const start = () =>
    run.mutate({ table: table.trim() }, { onSuccess: (r) => setSessionId(r.session_id) })
  const reset = () => {
    setSessionId(null)
    run.reset()
    setView('run')
  }
  // open a past run from History → load it into the run/review flow (E6/E7)
  const open = (id: string) => {
    setSessionId(id)
    setView('run')
  }

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-6 py-4">
          <div>
            <h1 className="text-xl font-semibold tracking-tight">geniefy-lite</h1>
            <p className="text-sm text-slate-500">
              Make your lakehouse AI ready.
            </p>
          </div>
          <div className="flex items-center gap-3">
            <UserCard />
            <nav className="flex items-center gap-1">
            {NAV.map((n) => (
              <button
                key={n.id}
                type="button"
                onClick={() => setView(n.id)}
                title={n.hint}
                className={cn(
                  'rounded-md px-3 py-1.5 text-sm font-medium transition',
                  view === n.id ? 'bg-slate-900 text-white' : 'text-slate-600 hover:bg-slate-100',
                )}
              >
                {n.label}
              </button>
            ))}
            {sessionId && (
              <button
                type="button"
                onClick={reset}
                className="ml-1 rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-100"
              >
                New run
              </button>
            )}
            </nav>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-5xl space-y-6 px-6 py-10">
        {view === 'history' && <HistoryView onOpen={open} />}
        {view === 'library' && <LibraryView />}
        {view === 'schema' && <SchemaRunView onOpen={open} />}
        {view === 'run' && (
          <>
        {!sessionId && (
          <>
          <div className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
            <label htmlFor="table" className="block text-sm font-medium text-slate-700">
              Table to document
            </label>
            <div className="mt-2 flex gap-2">
              <input
                id="table"
                value={table}
                onChange={(e) => setTable(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && valid && start()}
                placeholder="catalog.schema.table"
                className="flex-1 rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-confidence-high focus:outline-none focus:ring-1 focus:ring-confidence-high"
              />
              <button
                type="button"
                onClick={start}
                disabled={!valid || run.isPending}
                className={cn(
                  'rounded-md px-4 py-2 text-sm font-medium text-white transition',
                  valid && !run.isPending ? 'bg-slate-900 hover:bg-slate-700' : 'cursor-not-allowed bg-slate-300',
                )}
              >
                {run.isPending ? 'Starting…' : 'Document'}
              </button>
            </div>
            <p className="mt-2 text-xs text-slate-500">
              One table — try{' '}
              <button
                type="button"
                onClick={() => setTable('samples.tpch.orders')}
                className="font-mono text-indigo-600 hover:underline"
              >
                samples.tpch.orders
              </button>
              . To document an entire schema at once, use the <span className="font-medium">Schema</span> tab.
            </p>
            {run.isError && (
              <p className="mt-2 text-xs text-red-600">Couldn't start the run. Check the table name and try again.</p>
            )}
          </div>

          <HowItWorks />
          </>
        )}

        {sessionId && isLoading && <p className="text-sm text-slate-500">Loading session…</p>}
        {session && IN_FLIGHT.includes(session.status) && !hasDrafts && <RunView session={session} />}
        {session && session.status === 'failed' && (
          <div className="rounded-xl border border-red-200 bg-red-50 p-5 text-sm text-red-700">
            The run failed. Check table access (SELECT) and the warehouse/endpoint, then start a new run.
          </div>
        )}
        {session && sessionId && (DONE.includes(session.status) || (IN_FLIGHT.includes(session.status) && hasDrafts)) && (
          <ReviewView sessionId={sessionId} session={session} />
        )}
        {session && !IN_FLIGHT.includes(session.status) && !DONE.includes(session.status) &&
          session.status !== 'failed' && (
            <div className="rounded-xl border border-slate-200 bg-white p-5 text-sm text-slate-600">
              This run is <span className="font-medium">{session.status}</span>. Start a new run to continue.
            </div>
          )}
          </>
        )}
      </main>
    </div>
  )
}
