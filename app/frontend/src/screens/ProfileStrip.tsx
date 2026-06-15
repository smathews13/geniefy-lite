import type { ColumnProfile } from '../api/types'
import { MiniBars, NullFractionBar, Sparkline } from '../viz'

const fmt = (n: number) => n.toLocaleString()

/** Per-column profile "data fingerprint" (E4/U86, delight pass U96): a compact panel with a
 * null meter, distinct + uniqueness, numeric range, enum/PII badges, a frequency sparkline, and
 * a top-values distribution bar chart — read-only "evidence at a glance" beside each column draft.
 * Renders nothing when the profile carries no usable signal (e.g. an empty table). */
export function ProfileStrip({ profile }: { profile: ColumnProfile | undefined }) {
  if (!profile) return null
  const { null_fraction, distinct_count, cardinality_ratio, min, max, is_enum_candidate, top_k, pii } = profile
  const hasRange = min != null && max != null
  const tk = (Array.isArray(top_k) ? top_k : []).filter((t) => t && t.count != null)
  const bars = tk.slice(0, 6).map((t) => ({ label: String(t.value), value: Number(t.count) || 0 }))
  const counts = tk.map((t) => Number(t.count) || 0)
  const anything =
    null_fraction != null || distinct_count != null || hasRange || is_enum_candidate || pii?.detected || bars.length > 0
  if (!anything) return null

  return (
    <div className="mt-3 rounded-lg border border-slate-100 bg-slate-50/60 p-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[11px] text-slate-500">
        {null_fraction != null && <NullFractionBar fraction={null_fraction} />}
        {distinct_count != null && (
          <span className="inline-flex items-center gap-1.5">
            <span className="font-semibold tabular-nums text-slate-700">{fmt(distinct_count)}</span>
            <span>distinct</span>
            {cardinality_ratio != null && (
              <span className="rounded-full bg-white px-1.5 py-0.5 text-[10px] font-medium text-slate-500 ring-1 ring-slate-200">
                {(cardinality_ratio * 100).toFixed(cardinality_ratio < 0.1 ? 1 : 0)}% unique
              </span>
            )}
          </span>
        )}
        {hasRange && (
          <span className="inline-flex items-center gap-1.5">
            <span className="text-slate-400">range</span>
            <span className="font-mono text-slate-600">{String(min)}</span>
            <span className="text-slate-300">→</span>
            <span className="font-mono text-slate-600">{String(max)}</span>
          </span>
        )}
        {is_enum_candidate && (
          <span className="rounded-full bg-indigo-50 px-2 py-0.5 font-medium text-indigo-700 ring-1 ring-indigo-100">
            enum
          </span>
        )}
        {pii?.detected && (
          <span className="rounded-full bg-rose-50 px-2 py-0.5 font-medium text-rose-700 ring-1 ring-rose-100">
            PII{pii.classes && pii.classes.length > 0 ? `: ${pii.classes.join(', ')}` : ''}
          </span>
        )}
        {counts.length >= 3 && (
          <span className="ml-auto opacity-70" title="value-frequency shape">
            <Sparkline data={counts} width={64} height={18} color="#6366f1" />
          </span>
        )}
      </div>

      {bars.length > 0 && (
        <div className="mt-2.5">
          <p className="mb-1 text-[10px] font-medium uppercase tracking-wide text-slate-400">Top values</p>
          <MiniBars items={bars} />
        </div>
      )}
    </div>
  )
}
