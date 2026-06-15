import { scaleLinear } from '@visx/scale'
import { LinePath } from '@visx/shape'

/** A compact distribution sparkline (U9 §3), drawn with visx (D31). */
export function Sparkline({
  data,
  width = 80,
  height = 24,
  color = '#64748b',
}: {
  data: number[]
  width?: number
  height?: number
  color?: string
}) {
  if (data.length < 2) return null
  const xs = scaleLinear<number>({ domain: [0, data.length - 1], range: [1, width - 1] })
  const ys = scaleLinear<number>({
    domain: [Math.min(...data), Math.max(...data)],
    range: [height - 1, 1],
  })
  const points = data.map((d, i) => [i, d] as const)
  return (
    <svg width={width} height={height} role="img" aria-label="distribution sparkline">
      <LinePath<readonly [number, number]>
        data={points}
        x={(d) => xs(d[0])}
        y={(d) => ys(d[1])}
        stroke={color}
        strokeWidth={1.5}
        fill="none"
      />
    </svg>
  )
}
