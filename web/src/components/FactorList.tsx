import type { Factor, Report } from '../types/contracts'

const STATUS_LABEL: Record<string, string> = {
  covered: 'In our data',
  searching: 'Searching',
  found: 'Found',
  proxy: 'Proxy',
  dropped: 'Dropped',
}
const PRIORITY_LABEL: Record<string, string> = { high: 'High priority', medium: 'Medium priority', low: 'Low priority' }

interface Props {
  factors: Factor[]
  report: Report | null
}

export default function FactorList({ factors, report }: Props) {
  if (factors.length === 0) {
    return (
      <section className="section">
        <h2>What matters for this practice</h2>
        <p className="hint">Factors appear here as the agent works out what makes a new office succeed for this practice.</p>
      </section>
    )
  }
  return (
    <section className="section">
      <h2>What matters for this practice</h2>
      <ul className="factors">
        {factors.map((f) => {
          const source = f.feature_id ? report?.sources?.[f.feature_id] : undefined
          return (
            <li className="factor" key={f.id}>
              <h3>{f.factor}</h3>
              <p className="proxy">{f.proxy}</p>
              <div className="chips">
                <span className="chip" data-status={f.status}>
                  {STATUS_LABEL[f.status ?? 'searching']}
                </span>
                <span className="chip">{PRIORITY_LABEL[f.priority]}</span>
                <span className="chip">{f.direction === 1 ? 'More is better' : 'Less is better'}</span>
                {source && (
                  <a className="source" href={source} target="_blank" rel="noreferrer">
                    Source
                  </a>
                )}
              </div>
              {f.caveat && <p className="caveat">{f.caveat}</p>}
            </li>
          )
        })}
      </ul>
    </section>
  )
}
