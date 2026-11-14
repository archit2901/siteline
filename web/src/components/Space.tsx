import type { Strategy } from '../api/client'

const usd = (v: number) => (v >= 1e6 ? `$${(v / 1e6).toFixed(2)}M` : `$${Math.round(v / 1000).toLocaleString('en-US')}k`)
const LINE: Record<string, string> = {
  xray: 'X-ray', mri: 'MRI', physical_therapy: 'Physical therapy', asc: 'Surgery center', urgent_care: 'Walk-in / urgent care',
  cold_storage: 'Cold storage', loading: 'Loading', kitchen: 'Kitchen',
}

/** Space program, visibility need and lease / buy / build per top space (Python, planning assumptions). */
export default function Space({ s, onOpen }: { s: Strategy; onOpen: (siteId: string) => void }) {
  const p = s.program
  return (
    <div className="space">
      <div className="callout">
        <div className="kicker"><span>Space program</span></div>
        <p><b>{p.recommended_sqft[0].toLocaleString('en-US')}–{p.recommended_sqft[1].toLocaleString('en-US')} sq ft</b>
          {p.service_lines.length ? <> for {p.service_lines.map((l) => LINE[l] ?? l).join(', ')}</> : <> with standard rooms</>}
          {p.special_buildout_usd > 0 && <>; about {usd(p.special_buildout_usd)} of specialized buildout</>}.</p>
        {p.requirements.length > 0 && <div className="pills">{p.requirements.map((r) => <span key={r} className="pill static">{r}</span>)}</div>}
      </div>
      <div className="callout">
        <div className="kicker"><span>Visibility need: {s.visibility}</span></div>
        <p>{s.visibility_reason[0].toUpperCase() + s.visibility_reason.slice(1)}. {s.visibility === 'low'
          ? 'Office, medical and flex space rank as well as retail frontage.'
          : `Listings in ${s.preferred_space_uses.join(', ')} space rank higher.`}</p>
      </div>
      <div className="callout">
        <div className="kicker"><span>Lease, buy or build</span></div>
        <p>{s.recommendation}</p>
        <table className="tenure">
          <thead><tr><th>Space</th><th>Lease /yr</th><th>Buy /yr</th><th>Build /yr</th></tr></thead>
          <tbody>{s.sites.map((row) => {
            const o = Object.fromEntries(row.options.map((x) => [x.option, x]))
            const cell = (k: string) => (o[k] ? <td className={row.cheapest === k ? 'best' : ''}>{usd(o[k].annual_cost_usd)}</td> : <td>—</td>)
            return (
              <tr key={row.site_id}>
                <td><button className="linkbtn" style={{ margin: 0 }} onClick={() => onOpen(row.site_id)}>{row.address}</button>{row.sqft ? <span className="why"> · {row.sqft.toLocaleString('en-US')} sq ft</span> : null}</td>
                {cell('lease')}{cell('buy')}{cell('build')}
              </tr>
            )
          })}</tbody>
        </table>
        <p className="why">Planning assumptions, not quotes: building value = rent ÷ {(s.assumptions.cap_rate * 100).toFixed(1)}% cap rate;
          {' '}{Math.round(s.assumptions.loan_to_value * 100)}% loan at {(s.assumptions.mortgage_rate * 100).toFixed(1)}%; property tax
          {' '}{(s.assumptions.property_tax_rate * 100).toFixed(1)}%; build at ${s.assumptions.build_cost_psf}/sq ft; buildout written off over the
          {' '}{s.assumptions.lease_term_years}-year lease, or its {s.assumptions.improvement_life_years}-year life if owned.</p>
      </div>
    </div>
  )
}
