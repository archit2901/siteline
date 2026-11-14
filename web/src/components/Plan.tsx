import type { Scout, Todo, TraceLine } from '../state/runReducer'
import type { Factor } from '../types/contracts'

const SCOUT_LABEL: Record<Scout['status'], string> = {
  running: 'Searching',
  retrying: 'Trying another source',
  ready: 'Dataset ready',
  found: 'Found',
  proxy: 'Using a proxy',
  dropped: 'Dropped',
}

interface Props {
  todos: Todo[]
  scouts: Scout[]
  factors: Record<string, Factor>
  trace: TraceLine[]
}

export default function Plan({ todos, scouts, factors, trace }: Props) {
  const recent = trace.slice(-6).reverse()
  return (
    <>
      <section className="section">
        <h2>Plan</h2>
        <ol className="todos">
          {todos.map((t) => (
            <li key={t.content} data-status={t.status}>
              <span className={`mark ${t.status}`} aria-label={t.status.replace('_', ' ')} />
              <span>{t.content}</span>
            </li>
          ))}
        </ol>
      </section>

      {scouts.length > 0 && (
        <section className="section">
          <h2>Data scouts</h2>
          <div className="scouts">
            {scouts.map((s) => (
              <div className="scout" key={s.factorId} data-status={s.status}>
                <div className="row">
                  <span>{factors[s.factorId]?.factor ?? s.factorId}</span>
                  <span className="tries">
                    {SCOUT_LABEL[s.status]}
                    {s.tries > 0 && `, try ${s.tries} of 3`}
                  </span>
                </div>
                {s.last && <p>{s.last}</p>}
              </div>
            ))}
          </div>
        </section>
      )}

      {recent.length > 0 && (
        <section className="section">
          <h2>Latest steps</h2>
          <ul className="trace">
            {recent.map((line) => (
              <li key={`${line.seq}-${line.kind}`} data-ok={line.ok}>
                <b>{line.agent.replace('dataset-scout:', 'scout: ')}</b> {line.kind === 'call' ? `runs ${line.tool}` : line.text}
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  )
}
