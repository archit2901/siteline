import { safeHref } from '../lib/safe'
import type { Report } from '../types/contracts'
import Icon from './Icon'

const TRIP: Record<string, string> = {
  neighborhood: 'Neighborhood trip, about 10 minutes',
  local: 'Local trip, about 20 minutes',
  destination: 'Destination trip, up to 30 minutes',
}

interface ProfileProps {
  report: Report | null
  summary: string | null
}

/** What the business-profiler decided: customers, competitors, trip length, space types. */
export function BusinessProfile({ report, summary }: ProfileProps) {
  const demand = report?.demand
  if (!demand && !summary) return null
  return (
    <section className="section profile">
      <h2>Who this business serves</h2>
      {demand ? (
        <>
          {demand.rationale && <p className="lede">{demand.rationale}</p>}
          <dl>
            <dt>Customers</dt>
            <dd className="segments">
              {(demand.segments ?? []).map((s) => (
                <span key={s.feature_id} className="chip">
                  {s.label}
                  {s.weight != null && s.weight < 1 ? ` (${Math.round(s.weight * 100)}%)` : ''}
                </span>
              ))}
            </dd>
            <dt>Counted as</dt>
            <dd>{demand.demand_label}</dd>
            {demand.competitor_label && (
              <>
                <dt>Competitors</dt>
                <dd>{demand.competitor_label}</dd>
              </>
            )}
            {demand.trip && (
              <>
                <dt>How far they travel</dt>
                <dd>{TRIP[demand.trip] ?? demand.trip}</dd>
              </>
            )}
            {(demand.space_uses ?? []).length > 0 && (
              <>
                <dt>Space types</dt>
                <dd>{(demand.space_uses ?? []).join(', ')}</dd>
              </>
            )}
          </dl>
        </>
      ) : (
        <p className="lede" style={{ marginTop: 8 }}>
          {summary}
        </p>
      )}
    </section>
  )
}

/** Report-wide caveats and every data source the run used. */
export function ReportNotes({ report, featureLabels }: { report: Report; featureLabels: Record<string, string> }) {
  const sources = Object.entries(report.sources ?? {})
  const caveats = report.caveats ?? []
  return (
    <>
      <section className="section">
        <h2>How to read these results</h2>
        {caveats.length ? (
          <ul className="notes">
            {caveats.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        ) : (
          <p className="hint">No caveats for this run.</p>
        )}
      </section>
      <section className="section">
        <h2>Data sources</h2>
        <p className="lede">Every factor on the map comes from one of these public datasets. Spaces come from CoStar.</p>
        <ul className="sources" style={{ marginTop: 8 }}>
          {sources.map(([feature, url]) => {
            const href = safeHref(url)
            return (
              <li key={feature}>
                {featureLabels[feature] ?? feature}
                {href && (
                  <a href={href} target="_blank" rel="noreferrer">
                    {href.replace(/^https?:\/\//, '')} <Icon name="external" size={12} />
                  </a>
                )}
              </li>
            )
          })}
        </ul>
      </section>
    </>
  )
}
