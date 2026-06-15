import { PolarAngleAxis, PolarGrid, Radar, RadarChart, ResponsiveContainer } from 'recharts'
import type { JudgeScores } from '../api/types'

/** The Judge's rubric subscores as a small radar (U9 §6 explainability). */
export function JudgeRadar({ scores }: { scores: JudgeScores }) {
  const data = Object.entries(scores.subscores).map(([dim, value]) => ({
    dimension: dim.replace(/_/g, ' '),
    value,
  }))
  return (
    <div className="h-48 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <RadarChart data={data} outerRadius="70%">
          <PolarGrid />
          <PolarAngleAxis dataKey="dimension" tick={{ fontSize: 10, fill: '#475569' }} />
          <Radar dataKey="value" stroke="#16a34a" fill="#16a34a" fillOpacity={0.3} />
        </RadarChart>
      </ResponsiveContainer>
    </div>
  )
}
