import { useEffect, useRef, useState } from 'react'
import { useApply, useApproveHighConfidence, useConfig, useRegenerate } from '../api/hooks'
import { TABLE_TARGET, type ColumnProfile, type SessionView, type TableDraft } from '../api/types'
import { ConfidenceChip, ReadinessMeter } from '../viz'
import { DraftCard } from './DraftCard'
import { QuestionsPanel } from './QuestionsPanel'

interface CardSpec {
  target: string
  title: string
  subtitle?: string
  draft: TableDraft
  profile?: ColumnProfile
}

/** The interactive review + apply screen (U6 / U9 §6). */
export function ReviewView({ sessionId, session }: { sessionId: string; session: SessionView }) {
  const apply = useApply(sessionId)
  const approveHi = useApproveHighConfidence(sessionId)
  const regen = useRegenerate(sessionId)
  const cfg = useConfig()
  const keepThreshold = cfg.data?.keep_threshold ?? 0.75

  // Regenerate grays ONLY the targeted cards (U95): track them, and clear once the run settles
  // (status leaves the in-flight set). ReviewView stays mounted during a regenerate (App keeps it
  // up while drafts exist), so other cards stay fully interactive.
  const [regenerating, setRegenerating] = useState<Set<string>>(new Set())
  const inFlight = ['created', 'profiling', 'gathering_context', 'reasoning', 'applying'].includes(session.status)
  const prevStatus = useRef(session.status)
  useEffect(() => {
    if (prevStatus.current !== session.status && !inFlight) setRegenerating(new Set())
    prevStatus.current = session.status
  }, [session.status, inFlight])

  // index the per-column profile (E4/U86) by column name for "evidence at a glance"
  const profileByCol = new Map<string, ColumnProfile>(
    (session.profile?.columns ?? []).map((p) => [p.name, p]),
  )

  const cards: CardSpec[] = [
    ...(session.table_draft
      ? [{ target: TABLE_TARGET, title: 'Table comment', subtitle: session.target, draft: session.table_draft }]
      : []),
    ...session.column_drafts.map((c) => ({
      target: c.column_name,
      title: c.column_name,
      subtitle: c.data_type ?? undefined,
      draft: c as TableDraft,
      profile: profileByCol.get(c.column_name),
    })),
  ]

  const doRegen = (targets: string[]) => {
    setRegenerating((prev) => new Set([...prev, ...targets]))
    if (targets.length >= cards.length) regen.mutate({ all: true })
    else regen.mutate({ targets })
  }
  const anyRegenerating = regenerating.size > 0

  const approvable = cards.filter((c) => c.draft.status === 'approved' || c.draft.status === 'edited').length
  const appliedCount = cards.filter((c) => c.draft.apply_status === 'applied').length
  // readiness "glow-up" (U9 §5): share of drafts written to UC
  const readiness = cards.length ? appliedCount / cards.length : 0
  const tableCard = cards.find((c) => c.target === TABLE_TARGET)
  const columnCards = cards.filter((c) => c.target !== TABLE_TARGET)

  // Overall confidence (LLD-amend-007 §2 / D59): weighted rollup of the Judge's per-draft scores
  // (anchor the table comment + the column mean, null-safe) — a distinct axis from the AI-ready ring
  // above. ALWAYS rendered with the breakdown + weakest caveat so a single number can't hide a weak
  // draft (D22/D23). Buckets use keep_threshold; the ConfidenceChip's color band is display-only.
  const tableConf = tableCard?.draft.confidence ?? null
  const colConfs = columnCards.map((c) => c.draft.confidence).filter((x): x is number => x != null)
  const colMean = colConfs.length ? colConfs.reduce((a, b) => a + b, 0) / colConfs.length : null
  const overall =
    tableConf != null && colMean != null ? 0.5 * tableConf + 0.5 * colMean : tableConf ?? colMean
  const reviewReady = cards.filter(
    (c) =>
      ['draft', 'approved', 'edited'].includes(c.draft.status) &&
      c.draft.confidence != null &&
      c.draft.confidence >= keepThreshold,
  ).length
  const needsInput = cards.filter((c) => c.draft.status === 'needs_input').length
  const lowConf = cards.filter((c) => c.draft.status === 'low_confidence').length
  const scored = cards.filter((c) => c.draft.confidence != null)
  const weakest = scored.length
    ? scored.reduce((m, c) => ((c.draft.confidence ?? 1) < (m.draft.confidence ?? 1) ? c : m))
    : null
  // Bulk-approve set: only unflagged drafts above the threshold (matches the server, LLD §3 / D7).
  const highConfApprovable = cards.filter(
    (c) => c.draft.status === 'draft' && c.draft.confidence != null && c.draft.confidence >= keepThreshold,
  ).length

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="flex items-center gap-6">
          <ReadinessMeter value={readiness} />
          <div className="flex flex-col gap-0.5">
            <div className="flex items-center gap-2">
              <span className="text-sm text-slate-600">Confidence</span>
              <ConfidenceChip score={overall} />
            </div>
            <div className="text-xs text-slate-500">
              {reviewReady} review-ready · {needsInput} need input · {lowConf} low
            </div>
            {weakest && (
              <div className="text-xs text-slate-400">
                weakest: <span className="font-mono">{weakest.title}</span> @{' '}
                {Math.round((weakest.draft.confidence ?? 0) * 100)}%
              </div>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => approveHi.mutate()}
            disabled={!highConfApprovable || approveHi.isPending}
            className="rounded-md border border-confidence-high px-3 py-2 text-sm font-medium text-confidence-high transition hover:bg-emerald-50 disabled:cursor-not-allowed disabled:border-slate-200 disabled:text-slate-300"
            title={
              highConfApprovable
                ? 'Approve every high-confidence draft; flagged drafts still need your review'
                : 'Nothing above the confidence bar — review the flagged drafts individually'
            }
          >
            {approveHi.isPending ? 'Approving…' : `Approve ${highConfApprovable} high-confidence`}
          </button>
          <button
            type="button"
            onClick={() => doRegen(cards.map((c) => c.target))}
            disabled={anyRegenerating}
            className="rounded-md border border-slate-300 px-3 py-2 text-sm font-medium text-slate-600 transition hover:bg-slate-50 disabled:opacity-40"
            title="Re-generate every draft from the same profile + context"
          >
            {anyRegenerating ? 'Regenerating…' : 'Regenerate all'}
          </button>
          <button
            type="button"
            onClick={() => apply.mutate()}
            disabled={!approvable || apply.isPending}
            className="rounded-md bg-confidence-high px-4 py-2 text-sm font-medium text-white transition hover:brightness-110 disabled:cursor-not-allowed disabled:bg-slate-300"
          >
            {apply.isPending ? 'Applying…' : `Apply approved (${approvable})`}
          </button>
        </div>
      </div>

      {apply.isError && (
        <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          Apply failed — nothing was written to Unity Catalog. Check warehouse access (MODIFY) and retry.
        </p>
      )}

      {inFlight && (
        <div className="flex items-center gap-3 rounded-xl border border-indigo-200 bg-indigo-50/70 px-4 py-3 text-sm text-indigo-700">
          <span className="relative flex h-2.5 w-2.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-indigo-400 opacity-75" />
            <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-indigo-500" />
          </span>
          <span className="font-medium">✨ Generating comments…</span>
          <span className="text-indigo-500">
            {columnCards.length} column{columnCards.length === 1 ? '' : 's'} so far — hang tight while the rest load.
          </span>
        </div>
      )}

      {session.open_questions.length > 0 && (
        <QuestionsPanel sessionId={sessionId} questions={session.open_questions} />
      )}

      {tableCard && (
        <DraftCard
          key={tableCard.target}
          sessionId={sessionId}
          target={tableCard.target}
          title={tableCard.title}
          subtitle={tableCard.subtitle}
          draft={tableCard.draft}
          variant="table"
          onRegenerate={() => doRegen([tableCard.target])}
          regenerating={regenerating.has(tableCard.target)}
        />
      )}

      {columnCards.length > 0 && (
        // Columns read as secondary to the table hero (D53 #4): a left rail nests them beneath it.
        <div className="space-y-2.5 border-l-2 border-indigo-100 pl-3">
          <h3 className="pt-1 text-xs font-semibold uppercase tracking-wider text-slate-400">
            Columns ({columnCards.length})
          </h3>
          {columnCards.map((c) => (
            <DraftCard
              key={c.target}
              sessionId={sessionId}
              target={c.target}
              title={c.title}
              subtitle={c.subtitle}
              draft={c.draft}
              profile={c.profile}
              variant="column"
              onRegenerate={() => doRegen([c.target])}
              regenerating={regenerating.has(c.target)}
            />
          ))}
        </div>
      )}
    </div>
  )
}
