/** A tiny horizontal bar chart for categorical / top-k value frequencies (U9 §3 / U96).
 * Each row is a labeled bar whose width is proportional to its value vs the max — a compact,
 * delightful "distribution" glance for a column's most-common values. No deps; pure Tailwind+SVG-free. */
export function MiniBars({
  items,
  color = '#6366f1', // indigo-500
}: {
  items: { label: string; value: number }[]
  color?: string
}) {
  if (!items.length) return null
  const top = Math.max(1, ...items.map((i) => i.value))
  return (
    <div className="flex flex-col gap-1">
      {items.map((it, i) => (
        <div key={i} className="flex items-center gap-2">
          <span
            className="w-20 shrink-0 truncate text-right font-mono text-[10px] text-slate-500"
            title={it.label}
          >
            {it.label}
          </span>
          <div className="h-2 flex-1 overflow-hidden rounded-full bg-slate-200/70">
            <div
              className="h-2 rounded-full transition-[width] duration-500 ease-out"
              style={{ width: `${Math.max(2, (it.value / top) * 100)}%`, backgroundColor: color }}
            />
          </div>
          <span className="w-12 shrink-0 text-right text-[10px] tabular-nums text-slate-400">
            {it.value.toLocaleString()}
          </span>
        </div>
      ))}
    </div>
  )
}
