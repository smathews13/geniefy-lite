import { lazy, Suspense, useEffect, useState } from 'react'
import { useReview } from '../api/hooks'
import { cn } from '../lib/cn'
import type { ApplyStatus, ColumnProfile, DraftStatus, ReviewAction, StewardFacts, TableDraft } from '../api/types'
import { ConfidenceChip } from '../viz'
import { Pill, PillRow } from '../components/Pill'
import { ProfileStrip } from './ProfileStrip'

// recharts is heavy (~the bulk of the bundle) and only needed when a user opens "Why?";
// lazy-load it so it ships as its own chunk, not in the main bundle (U70 — U59-audit LOW).
const JudgeRadar = lazy(() => import('../viz/JudgeRadar').then((m) => ({ default: m.JudgeRadar })))

const STATUS_BADGE: Record<DraftStatus, string> = {
  draft: 'bg-slate-100 text-slate-600',
  needs_input: 'bg-amber-100 text-amber-800',
  low_confidence: 'bg-amber-100 text-amber-800',
  error: 'bg-red-100 text-red-700',
  reviewed: 'bg-slate-100 text-slate-600',
  edited: 'bg-sky-100 text-sky-800',
  approved: 'bg-green-100 text-green-800',
  applied: 'bg-green-600 text-white',
  rejected: 'bg-slate-200 text-slate-500 line-through',
}

const APPLY_BADGE: Partial<Record<ApplyStatus, string>> = {
  applied: 'applied to UC ✓',
  conflict: 'conflict — comment changed',
  failed: 'apply failed',
  unsupported: 'unsupported',
  skipped_noop: 'no change',
}

function StatusBadge({ status }: { status: DraftStatus }) {
  return (
    <span className={`rounded px-2 py-0.5 text-xs font-medium ${STATUS_BADGE[status]}`}>
      {status.replace(/_/g, ' ')}
    </span>
  )
}

// Steward-facts row for the hero (D53 §B4 / U114): scannable owner/freshness/grain/keys/sensitivity
// chips so a data owner reads the governance facts without parsing the prose. Renders only present facts.
const FACT_META: { key: keyof StewardFacts; icon: string; label: string }[] = [
  { key: 'owner', icon: '👤', label: 'Owner' },
  { key: 'freshness', icon: '🕐', label: 'Freshness' },
  { key: 'grain', icon: '▦', label: 'Grain' },
  { key: 'keys', icon: '🔑', label: 'Keys' },
  { key: 'sensitivity', icon: '🛡', label: 'Sensitivity' },
]

function StewardFactsRow({ facts }: { facts?: StewardFacts | null }) {
  if (!facts) return null
  const items = FACT_META.filter((m) => facts[m.key])
  if (items.length === 0) return null
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {items.map((m) => (
        <span
          key={m.key}
          className="inline-flex items-center gap-1 rounded-md bg-white/70 px-2 py-0.5 text-[11px] ring-1 ring-indigo-100"
        >
          <span aria-hidden>{m.icon}</span>
          <span className="font-medium text-slate-500">{m.label}:</span>
          <span className="text-slate-700">{facts[m.key]}</span>
        </span>
      ))}
    </div>
  )
}

/** One draft: current↔proposed diff, edit-in-place, approve/reject, and "why" (D23/U9 §6).
 * `draft` is the base shape — both table and column drafts are assignable. The **table** variant
 * renders as a steward-first hero (D53 #4): a prominent FQN + trust signal + table tags; the
 * **column** variant is a denser card with a data-type pill + tag pills. */
export function DraftCard({
  sessionId,
  target,
  title,
  subtitle,
  draft,
  profile,
  onRegenerate,
  regenerating = false,
  variant = 'column',
}: {
  sessionId: string
  target: string
  title: string
  subtitle?: string
  draft: TableDraft
  profile?: ColumnProfile
  onRegenerate?: () => void
  regenerating?: boolean
  variant?: 'table' | 'column'
}) {
  const review = useReview(sessionId)
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(draft.proposed_comment ?? '')
  const [why, setWhy] = useState(false)
  const tags = draft.tags ?? []

  // keep the edit buffer in sync when the proposed comment changes underneath us (e.g. a
  // resume re-draft), but never clobber an edit in progress (U70 — U59/U60-audit LOW).
  useEffect(() => {
    if (!editing) setText(draft.proposed_comment ?? '')
  }, [draft.proposed_comment, editing])

  const act = (action: ReviewAction, proposed_comment?: string) => {
    review.mutate({ target, action, proposed_comment }, { onSuccess: () => setEditing(false) })
  }

  const isTable = variant === 'table'

  return (
    <section
      className={cn(
        'rounded-xl border bg-white shadow-sm transition-all duration-300 hover:shadow-md',
        isTable
          ? 'overflow-hidden border-indigo-200 shadow-md ring-1 ring-indigo-100/70'
          : 'border-slate-200',
        regenerating && 'pointer-events-none animate-pulse opacity-60 ring-1 ring-indigo-200',
      )}
    >
      {/* ── header ──────────────────────────────────────────────────────── */}
      {isTable ? (
        // Steward-first hero band (D53 #4): the table's governance story up top — a data owner
        // lands on the FQN + trust + tags before drilling into columns.
        <div className="border-b border-indigo-100 bg-gradient-to-br from-indigo-50 to-white px-4 pb-3 pt-3">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-[10px] font-semibold uppercase tracking-wider text-indigo-500">
                Table · data steward view
              </p>
              <p className="truncate font-mono text-base font-semibold text-slate-800">{subtitle ?? title}</p>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              {regenerating && (
                <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-xs font-medium text-indigo-600">
                  Regenerating…
                </span>
              )}
              <span className="inline-flex items-center gap-1.5 rounded-full bg-white px-2 py-1 ring-1 ring-indigo-100">
                <span className="text-[10px] uppercase tracking-wide text-slate-400">trust</span>
                <ConfidenceChip score={draft.confidence} />
              </span>
              <StatusBadge status={draft.status} />
            </div>
          </div>
          <StewardFactsRow facts={draft.facts} />
          <PillRow tags={tags} className="mt-2" />
        </div>
      ) : (
        <div className="flex items-start justify-between gap-3 p-4 pb-0">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="font-mono text-xs text-slate-600">{title}</span>
              {subtitle && <Pill tone="type" title="data type">{subtitle}</Pill>}
            </div>
            <PillRow tags={tags} className="mt-1.5" />
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {regenerating && (
              <span className="rounded-full bg-indigo-50 px-2 py-0.5 text-xs font-medium text-indigo-600">
                Regenerating…
              </span>
            )}
            <ConfidenceChip score={draft.confidence} />
            <StatusBadge status={draft.status} />
          </div>
        </div>
      )}

      {/* ── body ────────────────────────────────────────────────────────── */}
      <div className={isTable ? 'px-4 pb-4 pt-3' : 'px-4 pb-4 pt-2'}>
        {/* current → proposed diff (D7: never silently clobber) */}
        {draft.current_comment && (
          <p className="text-xs text-slate-400 line-through">{draft.current_comment}</p>
        )}
        {editing ? (
          <div className="mt-2">
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={isTable ? 6 : 2}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:border-confidence-high focus:outline-none"
            />
            <div className="mt-1 flex gap-2">
              <button
                type="button"
                onClick={() => act('edit', text)}
                disabled={!text.trim() || review.isPending}
                className="rounded bg-slate-900 px-3 py-1 text-xs font-medium text-white disabled:opacity-40"
              >
                Save
              </button>
              <button
                type="button"
                onClick={() => setEditing(false)}
                className="rounded border border-slate-300 px-3 py-1 text-xs text-slate-600"
              >
                Cancel
              </button>
            </div>
          </div>
        ) : (
          <p className={cn('whitespace-pre-line text-slate-700', isTable ? 'mt-1 text-sm leading-relaxed' : 'mt-2 text-sm')}>
            {draft.proposed_comment ?? '—'}
          </p>
        )}

        {/* per-column profile evidence at a glance (E4/U86) — read-only */}
        <ProfileStrip profile={profile} />

        {/* actions + apply status */}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => act('approve')}
            disabled={review.isPending}
            className="rounded border border-green-300 px-2.5 py-1 text-xs font-medium text-green-700 hover:bg-green-50"
          >
            Approve
          </button>
          <button
            type="button"
            onClick={() => act('reject')}
            disabled={review.isPending}
            className="rounded border border-slate-300 px-2.5 py-1 text-xs text-slate-600 hover:bg-slate-50"
          >
            Reject
          </button>
          {!editing && (
            <button
              type="button"
              onClick={() => setEditing(true)}
              className="rounded border border-slate-300 px-2.5 py-1 text-xs text-slate-600 transition hover:bg-slate-50"
            >
              Edit
            </button>
          )}
          {onRegenerate && (
            <button
              type="button"
              onClick={onRegenerate}
              disabled={regenerating}
              className="rounded border border-slate-300 px-2.5 py-1 text-xs text-slate-600 transition hover:bg-slate-50 disabled:opacity-40"
              title="Re-generate this comment from the same profile + context"
            >
              {regenerating ? 'Regenerating…' : 'Regenerate'}
            </button>
          )}
          {(draft.judge_scores || draft.rationale) && (
            <button
              type="button"
              onClick={() => setWhy((v) => !v)}
              className="rounded px-2.5 py-1 text-xs text-slate-500 hover:bg-slate-100"
            >
              {why ? 'Hide why' : 'Why?'}
            </button>
          )}
          {APPLY_BADGE[draft.apply_status] && (
            <span className="ml-auto text-xs text-slate-500">{APPLY_BADGE[draft.apply_status]}</span>
          )}
        </div>

        {/* explainability (U9 §6): rationale + Judge subscores + issues */}
        {why && (
          <div className="mt-3 rounded-lg bg-slate-50 p-3">
            {draft.rationale && <p className="text-xs text-slate-600">{draft.rationale}</p>}
            {draft.evidence_refs.length > 0 && (
              <p className="mt-1 text-xs text-slate-400">
                Evidence: {draft.evidence_refs.join(', ')}
              </p>
            )}
            {draft.judge_scores && (
              <>
                <Suspense
                  fallback={
                    <div className="flex h-40 items-center justify-center rounded bg-slate-100 text-xs text-slate-400">
                      <span className="animate-pulse">Loading Judge radar…</span>
                    </div>
                  }
                >
                  <JudgeRadar scores={draft.judge_scores} />
                </Suspense>
                {draft.judge_scores.issues.length > 0 && (
                  <ul className="mt-1 list-inside list-disc text-xs text-amber-700">
                    {draft.judge_scores.issues.map((issue, i) => (
                      <li key={i}>{issue}</li>
                    ))}
                  </ul>
                )}
              </>
            )}
          </div>
        )}
      </div>
    </section>
  )
}
