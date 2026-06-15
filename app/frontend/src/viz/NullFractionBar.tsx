/** Null-fraction bar (U9 §3): how much of a column is NULL. */
export function NullFractionBar({ fraction }: { fraction: number }) {
  const pctNull = Math.round(Math.max(0, Math.min(1, fraction)) * 100)
  return (
    <div className="flex items-center gap-2" title={`${pctNull}% null`}>
      <div className="h-2 w-20 overflow-hidden rounded bg-slate-100">
        <div className="h-2 rounded bg-amber-400" style={{ width: `${pctNull}%` }} />
      </div>
      <span className="text-xs text-slate-500">{pctNull}% null</span>
    </div>
  )
}
