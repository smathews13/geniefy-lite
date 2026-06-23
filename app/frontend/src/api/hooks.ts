// TanStack Query hooks over the geniefy backend. The poll loop (D18) lives in
// `useSession`: it refetches while the run is in-flight and stops on a terminal status.
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { jsonFetch } from './client'
import type {
  AnswerInput,
  AppConfigView,
  LibraryEntry,
  ReviewAction,
  RunResponse,
  SchemaRun,
  SessionStatus,
  SessionSummary,
  SessionView,
} from './types'

function qs(params: Record<string, string | number | undefined>): string {
  const q = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== '') q.set(k, String(v))
  const s = q.toString()
  return s ? `?${s}` : ''
}

const POLL_MS = 1500
const IN_FLIGHT: SessionStatus[] = [
  'created', 'profiling', 'gathering_context', 'reasoning', 'applying',
]

export function useMe() {
  return useQuery({
    queryKey: ['me'],
    queryFn: () => jsonFetch<{ email: string | null; username: string | null; user_id: string | null; actor: string }>('/api/me'),
    staleTime: Infinity,
  })
}

export function useConfig() {
  return useQuery({
    queryKey: ['config'],
    queryFn: () => jsonFetch<AppConfigView>('/api/config'),
    staleTime: Infinity,
  })
}

export function useRun() {
  return useMutation({
    mutationFn: (vars: { table: string; mode?: string }) =>
      jsonFetch<RunResponse>('/api/run', { method: 'POST', body: JSON.stringify(vars) }),
  })
}

/** Session history (E6/E7) — newest first; optional status/table filters + pagination. */
export function useSessions(params: { status?: string; table?: string; limit?: number; offset?: number } = {}) {
  return useQuery({
    queryKey: ['sessions', params],
    queryFn: () => jsonFetch<{ sessions: SessionSummary[] }>(`/api/sessions${qs(params)}`),
  })
}

/** Reusable comment library (E6/D52) — most-used first; sunset hidden unless include_sunset. */
export function useLibrary(
  params: { scope?: string; include_sunset?: boolean; limit?: number; offset?: number } = {},
) {
  const { include_sunset, ...rest } = params
  const query = { ...rest, ...(include_sunset ? { include_sunset: 'true' } : {}) }
  return useQuery({
    queryKey: ['library', params],
    queryFn: () => jsonFetch<{ entries: LibraryEntry[] }>(`/api/library${qs(query)}`),
  })
}

/** Soft-retire a library entry (D52 §A5) — excluded from reuse + the default view; revivable. */
export function useSunsetLibrary() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => jsonFetch(`/api/library/${encodeURIComponent(id)}/sunset`, { method: 'POST' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['library'] }),
  })
}

/** Revive a sunset entry → 'approved' (D52 refinement). */
export function useReviveLibrary() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => jsonFetch(`/api/library/${encodeURIComponent(id)}/revive`, { method: 'POST' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['library'] }),
  })
}

/** Poll a session until it reaches a terminal status (D18). */
export function useSession(sessionId: string | null) {
  return useQuery({
    queryKey: ['session', sessionId],
    queryFn: () => jsonFetch<SessionView>(`/api/sessions/${sessionId}`),
    enabled: !!sessionId,
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status && IN_FLIGHT.includes(status) ? POLL_MS : false
    },
  })
}

function useSessionMutation<TVars>(
  sessionId: string,
  mutationFn: (vars: TVars) => Promise<unknown>,
) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['session', sessionId] }),
  })
}

export function useAnswers(sessionId: string) {
  return useSessionMutation<AnswerInput[]>(sessionId, (answers) =>
    jsonFetch(`/api/sessions/${sessionId}/answers`, {
      method: 'POST',
      body: JSON.stringify({ answers }),
    }),
  )
}

export function useReview(sessionId: string) {
  return useSessionMutation<{ target: string; action: ReviewAction; proposed_comment?: string }>(
    sessionId,
    ({ target, action, proposed_comment }) =>
      jsonFetch(`/api/sessions/${sessionId}/drafts/${encodeURIComponent(target)}/review`, {
        method: 'POST',
        body: JSON.stringify({ action, proposed_comment }),
      }),
  )
}

/** Regenerate the table and/or specific columns (E3/D45) — async; the session goes in-flight
 * and the `useSession` poll picks up the re-draft. `targets` omitted + `all: true` ⇒ everything. */
export function useRegenerate(sessionId: string) {
  return useSessionMutation<{ targets?: string[]; all?: boolean }>(sessionId, (body) =>
    jsonFetch(`/api/sessions/${sessionId}/regenerate`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  )
}

export function useApply(sessionId: string) {
  return useSessionMutation<void>(sessionId, () =>
    jsonFetch(`/api/sessions/${sessionId}/apply`, { method: 'POST' }),
  )
}

/** Bulk-approve the high-confidence, unflagged drafts (LLD-amend-007 §3 / D59). The server selects
 * (status=draft & confidence>=keep_threshold); flagged drafts are left for explicit review, and
 * nothing is written to UC (approve != apply). Invalidates the session so the UI reflects approvals. */
export function useApproveHighConfidence(sessionId: string) {
  return useSessionMutation<void>(sessionId, () =>
    jsonFetch(`/api/sessions/${sessionId}/approve-high-confidence`, { method: 'POST' }),
  )
}

/** Per-table bulk-approve from the hands-off Schema view (LLD-amend-007 §4): approve a table's
 * high-confidence drafts WITHOUT opening it, then refresh the run so its rows update. Never applies. */
export function useApproveTableInRun(sessionId: string, runId: string) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () =>
      jsonFetch(`/api/sessions/${sessionId}/approve-high-confidence`, { method: 'POST' }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['schema-run', runId] }),
  })
}

// ── Hands-off schema runs (D51) ────────────────────────────────────────────
/** Recent schema runs (newest first). */
export function useSchemaRuns(params: { limit?: number; offset?: number } = {}) {
  return useQuery({
    queryKey: ['schema-runs', params],
    queryFn: () => jsonFetch<{ schema_runs: SchemaRun[] }>(`/api/schema-runs${qs(params)}`),
  })
}

/** One schema run + its per-table sessions; polls while the Job is enumerating/running (D18). */
export function useSchemaRun(runId: string | null) {
  return useQuery({
    queryKey: ['schema-run', runId],
    queryFn: () => jsonFetch<SchemaRun>(`/api/schema-runs/${runId}`),
    enabled: !!runId,
    refetchInterval: (query) => {
      const s = query.state.data?.status
      return s === 'enumerating' || s === 'running' ? POLL_MS : false
    },
  })
}

/** Point at a schema → trigger the batch Job (D51). Body: {catalog, schema, filters}. */
export function useStartSchemaRun() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (vars: { catalog: string; schema: string; filters?: Record<string, unknown> }) =>
      jsonFetch<{ schema_run_id: string }>('/api/schema-runs', { method: 'POST', body: JSON.stringify(vars) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['schema-runs'] }),
  })
}

/** Best-effort cancel of a running schema run. */
export function useCancelSchemaRun() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (runId: string) => jsonFetch(`/api/schema-runs/${runId}/cancel`, { method: 'POST' }),
    // U160: invalidate the list + only THIS run's detail (not every run-detail query).
    onSuccess: (_data, runId) => {
      qc.invalidateQueries({ queryKey: ['schema-runs'] })
      qc.invalidateQueries({ queryKey: ['schema-run', runId] })
    },
  })
}
