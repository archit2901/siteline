import { useState } from 'react'
import type { Recommendation, Report } from '../types/contracts'
import SourceLink from './SourceLink'

interface Props {
  report: Report
  featureLabels: Record<string, string>
  selectedSiteId: string | null
  onSelect: (id: string) => void
}

const money = (v: number) => `$${v.toFixed(2)}`

function singular(label: string): string {
  if (label.endsWith('children')) return `${label.slice(0, -'children'.length)}child`
  return label.endsWith('s') && !label.endsWith('ss') ? label.slice(0, -1) : label
}

function demandCaption(label: string | undefined): string {
  return !label || label === 'patients' ? 'net-new patients a year' : `${label} in reach would pick this site`
}

function availability(rec: Recommendation): string {
  if (rec.site.available === true) return 'For lease'
  if (rec.site.available === false) return 'Lease pending'
  return 'Candidate building'
}

/** One link per source: two model refs in a claim show one "Siteline model" link. */
function distinctSources(refs: string[]): string[] {
  const seen = new Set<string>()
  return refs.filter((ref) => {
    const [kind, rest = ''] = ref.split(':', 2)
    const key = kind === 'feature' ? ref : kind + rest.split('@', 2)[1]
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

export default function SiteCards({ report, featureLabels, selectedSiteId, onSelect }: Props) {
  const [collapsed, setCollapsed] = useState(false)
  return (
    <section className={`drawer${collapsed ? ' collapsed' : ''}`} aria-label="Top sites">
      <header>
        <h2>Top {report.recommendations.length} sites</h2>
        <span className="hint">{report.verified ? 'Every number checked against its source' : 'Numbers not yet verified'}</span>
        <button className="button collapse" onClick={() => setCollapsed((c) => !c)} aria-expanded={!collapsed}>
          {collapsed ? 'Show sites' : 'Hide sites'}
        </button>
      </header>
      <div className="cards">
        {report.recommendations.map((rec) => {
          const s = rec.site
          return (
            <article
              className="card"
              key={s.id}
              aria-pressed={s.id === selectedSiteId}
              tabIndex={0}
              onClick={() => onSelect(s.id)}
              onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && onSelect(s.id)}
            >
              <div className="top-line">
                <span className="rank">{rec.rank}</span>
                <div>
                  <div className="address">{s.address}</div>
                  <div className="sub">{s.submarket}</div>
                </div>
              </div>
              <div className="netnew tabular">{Math.round(rec.net_new_patients).toLocaleString('en-US')}</div>
              <div className="netnew-label">{demandCaption(report.demand_label)}</div>
              <dl className="facts tabular">
                <dt>Size</dt>
                <dt>Rent</dt>
                <dt>Floor</dt>
                <dt>Parking</dt>
                <dd>{s.sqft ? `${s.sqft.toLocaleString('en-US')} sq ft` : 'Not listed'}</dd>
                <dd>{s.rent_psf ? `${money(s.rent_psf)}/sq ft` : 'Not listed'}</dd>
                <dd>{s.floor ?? 'Not listed'}</dd>
                <dd>{s.parking_spaces != null ? `${s.parking_spaces}${s.parking_estimated ? ' (est.)' : ''}` : 'Not listed'}</dd>
              </dl>
              <div className="badges">
                <span className="chip">{availability(rec)}</span>
                {rec.annual_rent_per_customer != null && (
                  <span className="chip tabular">
                    ${rec.annual_rent_per_customer.toFixed(2)} rent per {singular(report.demand_label ?? 'patients')} a year
                  </span>
                )}
                {rec.stability != null && (
                  <span className="chip">Top 10 in {Math.round(rec.stability * 100)}% of weightings</span>
                )}
                {s.matterport_id && (
                  <a className="chip" href={`https://my.matterport.com/show/?m=${encodeURIComponent(s.matterport_id)}`} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>
                    3D tour
                  </a>
                )}
              </div>
              <ul className="claims">
                {rec.justification.map((claim) => (
                  <li key={claim.text}>
                    {claim.text}
                    <span className="refs">
                      {distinctSources(claim.evidence_refs).map((ref) => (
                        <SourceLink key={ref} evidenceRef={ref} report={report} featureLabels={featureLabels} />
                      ))}
                    </span>
                  </li>
                ))}
              </ul>
              {(rec.caveats ?? []).length > 0 && (
                <ul className="caveats">
                  {(rec.caveats ?? []).map((c) => (
                    <li key={c}>{c}</li>
                  ))}
                </ul>
              )}
            </article>
          )
        })}
      </div>
    </section>
  )
}
