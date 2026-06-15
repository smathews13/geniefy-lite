/** First-run onboarding for the Table tab's empty state (U129, D55). Two parts:
 *  - <FlowDiagram>: a "fun" animated architecture diagram — the geniefy-lite pipeline as an SVG
 *    with flowing dashed connectors + traveling dots (SMIL). The motion is decorative; the SVG
 *    carries a role=img + <title> so screen readers get the pipeline described once.
 *  - a 4-step explainer + it sits under the input so the empty home page teaches first-time users.
 *  Restrained-but-delightful per D23 — color is always paired with an icon/label, never alone. */

type Node = { x: number; label: string; sub: string; icon: string; highlight?: boolean }

const NODE_W = 150
const NODE_H = 72
const NODE_TOP = 24
const CENTER_Y = NODE_TOP + NODE_H / 2 // 60

// Five stages, evenly spaced across a 960-wide viewBox (margins 9, gaps 48).
const NODES: Node[] = [
  { x: 9, label: 'Table / Schema', sub: 'Unity Catalog', icon: '▦' },
  { x: 207, label: 'Profile + context', sub: 'sample · lineage · queries', icon: '🔍' },
  { x: 405, label: 'Reason', sub: 'Claude drafts', icon: '✨' },
  { x: 603, label: 'Judge + you', sub: 'score · clarify · approve', icon: '✓' },
  { x: 801, label: 'AI / Genie-ready', sub: 'documented + tagged', icon: '🚀', highlight: true },
]

// Connector segments live in the gaps between consecutive nodes.
const LINKS = NODES.slice(0, -1).map((n, i) => ({ from: n.x + NODE_W, to: NODES[i + 1].x, i }))

function FlowDiagram() {
  return (
    <svg
      viewBox="0 0 960 120"
      className="h-auto w-full"
      role="img"
      aria-labelledby="flow-title"
      preserveAspectRatio="xMidYMid meet"
    >
      <title id="flow-title">
        How geniefy-lite works: a Unity Catalog table or schema flows through profiling and context
        gathering, into Claude reasoning, then a quality judge plus your review, and out as an AI- and
        Genie-ready asset. Nothing is written to Unity Catalog until you approve it.
      </title>
      <defs>
        <linearGradient id="flow-ai" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="#6366f1" />
          <stop offset="100%" stopColor="#8b5cf6" />
        </linearGradient>
        <marker id="flow-arrow" markerWidth="7" markerHeight="7" refX="5.5" refY="3"
          orient="auto" markerUnits="userSpaceOnUse">
          <path d="M0,0 L6,3 L0,6 Z" fill="#a5b4fc" />
        </marker>
      </defs>

      {/* connectors: animated flowing dashes + a traveling dot, staggered per segment */}
      {LINKS.map(({ from, to, i }) => (
        <g key={`link-${i}`} aria-hidden="true">
          <line x1={from} y1={CENTER_Y} x2={to} y2={CENTER_Y} stroke="#c7d2fe" strokeWidth={2}
            strokeDasharray="5 5" markerEnd="url(#flow-arrow)">
            <animate attributeName="stroke-dashoffset" values="20;0" dur="1.2s" repeatCount="indefinite" />
          </line>
          <circle r={3.5} cy={CENTER_Y} fill="#6366f1">
            <animate attributeName="cx" values={`${from};${to}`} dur="1.6s"
              begin={`${i * 0.4}s`} repeatCount="indefinite" />
            <animate attributeName="opacity" values="0;1;1;0" dur="1.6s"
              begin={`${i * 0.4}s`} repeatCount="indefinite" />
          </circle>
        </g>
      ))}

      {/* nodes */}
      {NODES.map((n, i) => {
        const cx = n.x + NODE_W / 2
        return (
          <g key={`node-${i}`} aria-hidden="true">
            {n.highlight && (
              <rect x={n.x - 5} y={NODE_TOP - 5} width={NODE_W + 10} height={NODE_H + 10} rx={16}
                fill="#a5b4fc">
                <animate attributeName="opacity" values="0.15;0.45;0.15" dur="2.8s" repeatCount="indefinite" />
              </rect>
            )}
            <rect x={n.x} y={NODE_TOP} width={NODE_W} height={NODE_H} rx={12}
              fill={n.highlight ? 'url(#flow-ai)' : '#ffffff'}
              stroke={n.highlight ? 'none' : '#c7d2fe'} strokeWidth={1.5} />
            <text x={cx} y={NODE_TOP + 26} textAnchor="middle" fontSize={18}>{n.icon}</text>
            <text x={cx} y={NODE_TOP + 48} textAnchor="middle" fontSize={12} fontWeight={600}
              fill={n.highlight ? '#ffffff' : '#334155'}>{n.label}</text>
            <text x={cx} y={NODE_TOP + 62} textAnchor="middle" fontSize={8.5}
              fill={n.highlight ? '#e0e7ff' : '#94a3b8'}>{n.sub}</text>
          </g>
        )
      })}
    </svg>
  )
}

const STEPS: { icon: string; title: string; body: string }[] = [
  { icon: '🎯', title: 'Point it at your data',
    body: 'Give geniefy-lite a table — or a whole schema from the Schema tab. It reads the schema and a safe sample of the data.' },
  { icon: '🔍', title: 'It gathers grounding',
    body: 'It profiles the columns and pulls Unity Catalog lineage and popular queries, so every suggestion is evidence-based.' },
  { icon: '✨', title: 'Claude drafts the docs',
    body: 'A clear table summary, per-column comments, steward facts and tags — with questions raised wherever it is unsure.' },
  { icon: '✅', title: 'You review & apply',
    body: 'Edit, answer, approve. Nothing is written to Unity Catalog until you say so — you are always in control.' },
]

export function HowItWorks() {
  return (
    <section className="space-y-5" aria-label="How geniefy-lite works">
      <div className="rounded-xl border border-indigo-200 bg-gradient-to-br from-indigo-50/50 to-white p-6 shadow-sm">
        <h2 className="text-sm font-semibold text-slate-700">How geniefy-lite works</h2>
        <p className="mt-1 text-xs text-slate-500">
          From a raw table to an AI- and Genie-ready asset — you approve every change.
        </p>
        <div className="mt-4">
          <FlowDiagram />
        </div>
      </div>

      <ol className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {STEPS.map((s, i) => (
          <li key={s.title} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="flex items-center gap-2">
              <span className="flex h-6 w-6 items-center justify-center rounded-full bg-indigo-100 text-xs font-semibold text-indigo-700">
                {i + 1}
              </span>
              <span className="text-base" aria-hidden="true">{s.icon}</span>
            </div>
            <h3 className="mt-2 text-sm font-semibold text-slate-700">{s.title}</h3>
            <p className="mt-1 text-xs leading-relaxed text-slate-500">{s.body}</p>
          </li>
        ))}
      </ol>
    </section>
  )
}
