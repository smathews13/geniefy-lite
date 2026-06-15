// The consistent "confidence as visual language" (D23 P3): a [0,1] score → a band with a
// color, a LABEL, and a SHAPE/ICON — color is never the only signal (accessibility).

export type Band = 'low' | 'mid' | 'high'

export function band(score: number | null | undefined): Band {
  if (score == null) return 'low'
  if (score >= 0.8) return 'high'
  if (score >= 0.6) return 'mid'
  return 'low'
}

export const BAND_COLOR: Record<Band, string> = {
  low: '#64748b',
  mid: '#f59e0b',
  high: '#16a34a',
}

export const BAND_LABEL: Record<Band, string> = {
  low: 'Low',
  mid: 'Medium',
  high: 'High',
}

// shape also encodes the band, so the signal survives without color (D23 P3)
export const BAND_ICON: Record<Band, string> = {
  low: '○',
  mid: '◐',
  high: '●',
}

export function pct(score: number | null | undefined): string {
  return score == null ? '—' : `${Math.round(score * 100)}%`
}
