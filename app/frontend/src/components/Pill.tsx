import { cn } from '../lib/cn'

// Tag → hue. Free-form tags (D53/Q2), so we key off well-known semantics and fall back to slate.
// Sensitivity/PII tags get an alert hue so a steward spots them instantly.
const TAG_HUES: Record<string, string> = {
  pii: 'bg-rose-50 text-rose-700 ring-rose-200',
  sensitive: 'bg-rose-50 text-rose-700 ring-rose-200',
  sensitivity: 'bg-rose-50 text-rose-700 ring-rose-200',
  deprecated: 'bg-amber-50 text-amber-800 ring-amber-200',
  identifier: 'bg-indigo-50 text-indigo-700 ring-indigo-200',
  key: 'bg-indigo-50 text-indigo-700 ring-indigo-200',
  fk: 'bg-indigo-50 text-indigo-700 ring-indigo-200',
  metric: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  measure: 'bg-emerald-50 text-emerald-700 ring-emerald-200',
  dimension: 'bg-sky-50 text-sky-700 ring-sky-200',
  enum: 'bg-sky-50 text-sky-700 ring-sky-200',
  temporal: 'bg-violet-50 text-violet-700 ring-violet-200',
  fact: 'bg-teal-50 text-teal-700 ring-teal-200',
}
const DEFAULT_HUE = 'bg-slate-100 text-slate-600 ring-slate-200'

type PillTone = 'tag' | 'type' | 'neutral'

/** A small, rounded label. `tone='type'` is the data-type chip (mono, neutral); `tone='tag'`
 * colours by the tag's semantics (PII/sensitive → rose, identifier/key → indigo, …). */
export function Pill({ children, tone = 'neutral', title }: {
  children: string
  tone?: PillTone
  title?: string
}) {
  const hue =
    tone === 'tag' ? (TAG_HUES[children.toLowerCase()] ?? DEFAULT_HUE)
    : tone === 'type' ? 'bg-white text-slate-600 ring-slate-300 font-mono'
    : DEFAULT_HUE
  return (
    <span
      title={title}
      className={cn('inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium ring-1', hue)}
    >
      {children}
    </span>
  )
}

/** A wrapped row of tag pills (no-op when empty). */
export function PillRow({ tags, className }: { tags: string[]; className?: string }) {
  if (!tags || tags.length === 0) return null
  return (
    <div className={cn('flex flex-wrap gap-1', className)}>
      {tags.map((t, i) => (
        <Pill key={`${t}-${i}`} tone="tag">{t}</Pill>
      ))}
    </div>
  )
}
