import { band, BAND_COLOR, BAND_ICON, BAND_LABEL, pct } from './confidence'

/** Confidence as color + icon + value + label (D23 P3 — never color alone). */
export function ConfidenceChip({ score }: { score: number | null }) {
  const b = band(score)
  const color = BAND_COLOR[b]
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium"
      style={{ color, backgroundColor: `${color}1a` }}
      title={`${BAND_LABEL[b]} confidence`}
    >
      <span aria-hidden>{BAND_ICON[b]}</span>
      <span>{pct(score)}</span>
      <span className="sr-only">{BAND_LABEL[b]} confidence</span>
    </span>
  )
}
