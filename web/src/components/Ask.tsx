import { useState } from 'react'
import { ask } from '../api/client'
import type { AskFact, Report } from '../types/contracts'

const SUGGESTIONS = ['Why is #1 first?', 'Which has the most parking?', 'Compare #1 and #2', 'Which is cheapest per customer?']

interface Turn {
  question: string
  answer: string
  facts: AskFact[]
}

/** Strip the "#3 1200 Main St: " prefix when the rank dot already says which space it is. */
const factText = (f: AskFact) => (f.rank != null ? f.text.replace(/^#\d+ [^:]+:\s*/, '') : f.text)

/** Questions about this run, answered from facts Python renders from the verified report. */
export default function Ask({ report, onSelect }: { report: Report; onSelect: (siteId: string) => void }) {
  const [turns, setTurns] = useState<Turn[]>([])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)

  const send = async (question: string) => {
    const q = question.trim()
    if (!q || busy) return
    setText('')
    setBusy(true)
    try {
      const res = await ask(q, report)
      setTurns((t) => [...t, { question: q, answer: res.answer, facts: res.facts ?? [] }])
    } catch {
      setTurns((t) => [...t, { question: q, answer: 'I can’t reach the agent right now.', facts: [] }])
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="section ask">
      <h2>Ask about these results</h2>
      <p className="lede">Answers come only from this run's verified numbers. Select a space in an answer to open it.</p>
      <div className="suggest" role="group" aria-label="Suggested questions">
        {SUGGESTIONS.map((s) => (
          <button key={s} className="preset" disabled={busy} onClick={() => send(s)}>
            {s}
          </button>
        ))}
      </div>
      <ol className="answers" aria-live="polite">
        {turns.map((t, i) => (
          <li key={i}>
            <p className="bubble user">{t.question}</p>
            <div className="bubble assistant">
              <p>{t.answer}</p>
              {t.facts.length > 0 && (
                <ul className="facts-list">
                  {t.facts.map((f, k) => (
                    <li key={k}>
                      {f.rank != null && f.site_id ? (
                        <button className="rankdot" aria-label={`Open space ranked ${f.rank}`} onClick={() => onSelect(f.site_id!)}>
                          {f.rank}
                        </button>
                      ) : (
                        <span className="rankdot muted" aria-hidden="true" />
                      )}
                      <span>{factText(f)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </li>
        ))}
        {busy && <li className="bubble assistant typing">Looking through the results...</li>}
      </ol>
      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault()
          send(text)
        }}
      >
        <input value={text} placeholder="Which space has the lowest asking rent?" aria-label="Ask about the results" onChange={(e) => setText(e.target.value)} />
        <button className="button primary" type="submit" disabled={busy || !text.trim()}>
          Ask
        </button>
      </form>
    </section>
  )
}
