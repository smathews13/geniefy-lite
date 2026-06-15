/** Radial "AI-ready" meter (U9 §5 readiness meter): % of columns documented & applied. */
export function ReadinessMeter({ value, label = 'AI-ready' }: { value: number; label?: string }) {
  const v = Math.max(0, Math.min(1, value))
  const r = 28
  const circ = 2 * Math.PI * r
  const offset = circ * (1 - v)
  return (
    <div className="inline-flex items-center gap-2" title={`${Math.round(v * 100)}% ${label}`}>
      <svg width="72" height="72" viewBox="0 0 72 72">
        <circle cx="36" cy="36" r={r} fill="none" stroke="#e2e8f0" strokeWidth="8" />
        <circle
          cx="36"
          cy="36"
          r={r}
          fill="none"
          stroke="#16a34a"
          strokeWidth="8"
          strokeLinecap="round"
          strokeDasharray={circ}
          strokeDashoffset={offset}
          transform="rotate(-90 36 36)"
        />
        <text x="36" y="40" textAnchor="middle" className="fill-slate-700 text-sm font-semibold">
          {Math.round(v * 100)}%
        </text>
      </svg>
      <span className="text-sm text-slate-600">{label}</span>
    </div>
  )
}
