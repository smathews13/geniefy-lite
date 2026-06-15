import { useState } from 'react'
import { useAnswers } from '../api/hooks'
import { cn } from '../lib/cn'
import type { Question } from '../api/types'

/** The agent's low-confidence questions (awaiting_input). Submitting answers resumes the
 * run (U4 §3.6 / U6) via useAnswers → the session re-polls. */
export function QuestionsPanel({ sessionId, questions }: { sessionId: string; questions: Question[] }) {
  const answers = useAnswers(sessionId)
  const [text, setText] = useState<Record<string, string>>({})

  // pre-fill the LLM-suggested answer (D50/U100); the user can accept or edit it.
  const valueFor = (q: Question) => text[q.id] ?? q.suggested_answer ?? ''
  const isSuggested = (q: Question) => text[q.id] === undefined && !!q.suggested_answer

  const submit = () => {
    const payload = questions
      .map((q) => ({ question_id: q.id, text: valueFor(q).trim() }))
      .filter((p) => p.text)
    if (payload.length) answers.mutate(payload)
  }

  return (
    <section className="rounded-xl border border-amber-300 bg-amber-50 p-5">
      <h2 className="text-sm font-semibold text-amber-900">
        The agent needs your input ({questions.length})
      </h2>
      <div className="mt-3 space-y-3">
        {questions.map((q) => (
          <div key={q.id}>
            <p className="text-sm text-amber-900">
              {q.text}
              {q.target_name && (
                <span className="ml-2 font-mono text-xs text-amber-700">{q.target_name}</span>
              )}
            </p>
            <div className="mt-1 flex items-center gap-2">
              <input
                value={valueFor(q)}
                onChange={(e) => setText({ ...text, [q.id]: e.target.value })}
                placeholder="Your answer…"
                className={cn(
                  'w-full rounded-md border bg-white px-3 py-1.5 text-sm focus:outline-none',
                  isSuggested(q)
                    ? 'border-indigo-300 italic text-slate-500 focus:border-indigo-500 focus:not-italic'
                    : 'border-amber-300 focus:border-amber-500',
                )}
              />
              {isSuggested(q) && (
                <span className="shrink-0 rounded-full bg-indigo-50 px-2 py-0.5 text-[10px] font-medium text-indigo-600">
                  ✨ suggested
                </span>
              )}
            </div>
          </div>
        ))}
      </div>
      <button
        type="button"
        onClick={submit}
        disabled={answers.isPending}
        className="mt-3 rounded-md bg-amber-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-amber-700 disabled:opacity-50"
      >
        {answers.isPending ? 'Submitting…' : 'Submit answers'}
      </button>
    </section>
  )
}
