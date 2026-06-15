// TS mirror of the backend response shapes (geniefy_app.api / geniefy_core.state).
// Kept in lock-step with the enums in migrations/001_init.sql.

export type SessionStatus =
  | 'created' | 'profiling' | 'gathering_context' | 'reasoning'
  | 'awaiting_input' | 'ready_for_review' | 'applying' | 'applied'
  | 'paused' | 'failed' | 'cancelled'

export type DraftStatus =
  | 'draft' | 'needs_input' | 'low_confidence' | 'error'
  | 'reviewed' | 'edited' | 'approved' | 'applied' | 'rejected'

export type ApplyStatus =
  | 'not_applied' | 'applied' | 'conflict' | 'failed' | 'unsupported' | 'skipped_noop'

export interface JudgeScores {
  subscores: Record<string, number>
  overall: number
  issues: string[]
}

interface DraftBase {
  current_comment: string | null
  proposed_comment: string | null
  rationale: string | null
  confidence: number | null
  judge_scores: JudgeScores | null
  evidence_refs: string[]
  tags: string[] // free-form LLM tags → pills (D53/U104)
  status: DraftStatus
  apply_status: ApplyStatus
  applied_comment: string | null
  applied_at: string | null
  applied_by: string | null
}

// Structured steward facts for the hero chips (D53 §B4 / U114) — UI-only; the prose comment is the
// UC artifact. Any subset may be present (the Reasoner omits unknowns).
export interface StewardFacts {
  owner?: string
  freshness?: string
  grain?: string
  keys?: string
  sensitivity?: string
}

export interface TableDraft extends DraftBase {
  facts?: StewardFacts | null
}

export interface ColumnDraft extends DraftBase {
  column_name: string
  ordinal: number | null
  data_type: string | null
  conditional_fields: Record<string, unknown> | null
}

export interface Question {
  id: string
  target_kind: 'table' | 'column'
  target_name: string | null
  text: string
  answered: boolean
  suggested_answer: string | null // LLM-proposed answer to pre-fill the box (D50/U100)
}

// Per-column profile evidence (geniefy_app.profiling) — exposed in the session view for
// the "evidence at a glance" viz (E4/U86). All fields optional: present only when profiled.
export interface ColumnTopK {
  value: unknown
  count: number
}

export interface ColumnProfile {
  name: string
  data_type?: string | null
  null_fraction?: number | null
  distinct_count?: number | null
  cardinality_ratio?: number | null
  min?: unknown
  max?: unknown
  is_enum_candidate?: boolean
  top_k?: ColumnTopK[]
  pii?: { detected: boolean; classes?: string[]; action?: string } | null
}

export interface ProfileView {
  table?: { row_count?: number; [k: string]: unknown }
  columns?: ColumnProfile[]
}

export interface SessionView {
  session_id: string
  target: string
  status: SessionStatus
  table_draft: TableDraft | null
  column_drafts: ColumnDraft[]
  open_questions: Question[]
  profile?: ProfileView
}

// History list rows (E6) — GET /api/sessions → { sessions: SessionSummary[] }
export interface SessionSummary {
  session_id: string
  target: string
  status: SessionStatus
  created_by: string
  created_at: string
  updated_at: string
  n_columns: number
  n_applied: number
}

// Comment-library rows (E6/D52) — GET /api/library → { entries: LibraryEntry[] }
export type LibraryStatus = 'approved' | 'applied' | 'sunset'

export interface LibraryEntry {
  id: string
  scope: 'column' | 'table'
  match_key: string
  canonical_comment: string
  tags: string[]
  usage_count: number
  source_table_ref: string | null
  approved_by: string | null
  updated_at: string | null
  status: LibraryStatus // lifecycle: approved → applied → sunset (D52/U103)
}

// Hands-off schema runs (D51) — GET /api/schema-runs[/{id}]
export type SchemaRunStatus =
  | 'enumerating' | 'running' | 'completed' | 'completed_with_errors' | 'failed' | 'cancelled'

export interface SchemaRunCounts {
  ready?: number; needs_input?: number; applied?: number; error?: number; skipped?: number
}

export interface SchemaRun {
  id: string
  catalog: string
  schema: string
  status: SchemaRunStatus
  filters: Record<string, unknown>
  total_tables: number | null
  counts: SchemaRunCounts
  job_run_id: number | null
  created_by: string
  created_at: string | null
  updated_at: string | null
  sessions?: SessionSummary[]   // attached by GET /api/schema-runs/{id}
}

export interface AppConfigView {
  model_endpoint: string
  mode: string
  keep_threshold: number
  template_id: string | null
  enabled_providers: string[]
}

export interface RunResponse {
  session_id: string
  status: string
}

export interface AnswerInput {
  question_id: string
  text: string
}

export type ReviewAction = 'approve' | 'reject' | 'edit'
export const TABLE_TARGET = '__table__'
