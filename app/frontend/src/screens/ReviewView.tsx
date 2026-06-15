import { useEffect, useRef, useState } from 'react'
import { useApply, useRegenerate } from '../api/hooks'
import { TABLE_TARGET, type ColumnProfile, type SessionView, type TableDraft } from '../api/types'
import { ReadinessMeter } from '../viz'
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
  const regen = useRegenerate(sessionId)

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

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <ReadinessMeter value={readiness} />
        <div className="flex items-center gap-2">
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
