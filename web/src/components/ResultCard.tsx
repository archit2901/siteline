// One recommended space for the customer view: what it would win, what it costs, and a plain verdict.
import { HAS_VEXCEL, aerialUrl } from '../lib/media'
import { verdict } from '../lib/verdict'
import type { Recommendation, Report } from '../types/contracts'
import Icon from './Icon'
import { money, singular, whole } from './SiteCards'

interface Props {
  rec: Recommendation
  report: Report
  selected: boolean
  onSelect: () => void
  onDetails: () => void
}

export default function ResultCard({ rec, report, selected, onSelect, onDetails }: Props) {
  const s = rec.site
  const label = report.demand_label ?? 'patients'
  const v = verdict(rec)
  const img = HAS_VEXCEL ? aerialUrl(s.lat, s.lon, 140, 360) : null
  return (
    <article className="result" data-selected={selected} onMouseEnter={onSelect} onFocus={onSelect}>
      {img && <img className="thumb" src={img} alt={`Aerial view of ${s.address}`} loading="lazy" />}
      <div className="result-body">
        <div className="result-top">
          <span className="rankdot">{rec.rank}</span>
          <span className="headline">{v.headline}</span>
        </div>
        <h3>{s.address}</h3>
        <p className="won">
          <b>{whole(rec.net_new_patients)}</b> {label === 'patients' ? 'new patients a year' : `${label} in reach`}
        </p>
        <p className="money">
          {s.rent_psf ? `${money(s.rent_psf)} per sq ft a year` : 'Rent not listed'}
          {s.sqft ? `, ${s.sqft.toLocaleString('en-US')} sq ft` : ''}
          {rec.annual_rent_per_customer != null && (
            <>
              {' '}
              ({money(rec.annual_rent_per_customer)} rent per {singular(label)} won)
            </>
          )}
        </p>
        {v.notes.length > 0 && (
          <ul className="notes-row">
            {v.notes.map((n) => (
              <li key={n.text} data-tone={n.tone}>
                {n.text}
              </li>
            ))}
          </ul>
        )}
        <div className="row">
          <button className="button primary" onClick={onDetails}>
            See details
          </button>
          {s.matterport_id && (
            <a className="button" href={`https://my.matterport.com/show/?m=${encodeURIComponent(s.matterport_id)}`} target="_blank" rel="noreferrer">
              <Icon name="cube" />
              3D tour
            </a>
          )}
        </div>
      </div>
    </article>
  )
}
