import { safeHref } from '../lib/safe'
import type { Factor, Report } from '../types/contracts'
import Icon from './Icon'

const STATUS_LABEL: Record<string, string> = {
  covered: 'In our data',
  searching: 'Searching',
  found: 'Found by a scout',
  proxy: 'Using a proxy',
  dropped: 'Dropped',
}
const PRIORITY_LABEL: Record<string, string> = { high: 'High priority', medium: 'Medium priority', low: 'Low priority' }

interface Props {
  factors: Factor[]
  report: Report | null
  /** factor id -> its scout's branch color, so a scouted factor keeps one color across the screen */
  scoutColors?: Record<string, string>
}

export default function FactorList({ factors, report, scoutColors = {} }: Props) {
  if (factors.length === 0) {
    return <p className="empty">Factors appear here as the agent works out what makes a new location succeed for this business.</p>
  }
  return (
    <section className="section">
      <h2>What matters for this business</h2>
      <p className="lede">The agent's reasoning. Python measures each factor across Houston; nothing here is a model-written number.</p>
      <ul style={{ marginTop: 6 }}>
        {factors.map((f) => {
          const source = f.feature_id ? safeHref(report?.sources?.[f.feature_id]) : undefined
          return (
            <li className="factor" key={f.id}>
              <h3>
                {f.factor}
                <span className="dir">{f.direction === 1 ? 'More is better' : 'Less is better'}</span>
              </h3>
              {f.why && <p className="why">{f.why}</p>}
              <p className="proxy">Measured as: {f.proxy}</p>
              <div className="chips">
                <span className={`chip${scoutColors[f.id] ? ' scouted' : ''}`} data-status={f.status} style={scoutColors[f.id] ? { ['--c' as string]: scoutColors[f.id] } : undefined}>
                  <span className="pip" aria-hidden="true" />
                  {STATUS_LABEL[f.status ?? 'searching']}
                </span>
                <span className="chip">{PRIORITY_LABEL[f.priority]}</span>
                {source && (
                  <a className="chip" href={source} target="_blank" rel="noreferrer">
                    Source <Icon name="external" size={14} />
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
