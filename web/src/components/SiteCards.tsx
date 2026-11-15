import { Fragment, useEffect, useRef, useState } from 'react'
import { HAS_VEXCEL, aerialUrl, countParkedCars, matterportScan, type CarCount, type ScanInfo } from '../lib/media'
import type { CommunityNeed, Recommendation, Report, ScoringSpec, SiteInspection } from '../types/contracts'
import Icon from './Icon'
import SourceLink from './SourceLink'
import WeightSliders from './WeightSliders'

export const money = (v: number) => `$${v.toFixed(2)}`
export const whole = (v: number) => Math.round(v).toLocaleString('en-US')

const IRREGULAR: Record<string, string> = { children: 'child', people: 'person', adults: 'adult', patients: 'patient' }
const QUALIFIERS = new Set(['under', 'over', 'aged', 'with', 'without'])

/** 'renter households' -> 'renter household', 'children under 18' -> 'child under 18' (same rule as critic.singular). */
export function singular(label: string): string {
  const words = label.split(' ')
  const i = words.findIndex((_, k) => k === words.length - 1 || QUALIFIERS.has(words[k + 1]) || /^\d/.test(words[k + 1]))
  const w = words[i]
  words[i] = IRREGULAR[w] ?? (w.endsWith('s') && !w.endsWith('ss') && w.length > 3 ? w.slice(0, -1) : w)
  return words.join(' ')
}

const isPatients = (label?: string) => !label || label === 'patients'

/** Column header and caption for the headline number: a forecast for healthcare, reach for every other business. */
export function demandCaption(label: string | undefined): string {
  return isPatients(label) ? 'net-new patients a year' : `${label} in reach would pick this site`
}

export function availability(rec: Recommendation): { text: string; pending: boolean } {
  if (rec.site.available === true) return { text: 'For lease', pending: false }
  if (rec.site.available === false) return { text: 'Lease pending', pending: true }
  return { text: 'Candidate building', pending: true }
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

interface TuneProps {
  spec: ScoringSpec
  featureLabels: Record<string, string>
  busy: boolean
  onChange: (weights: Record<string, number>) => void
}

interface TableProps {
  report: Report
  selectedSiteId: string | null
  onSelect: (id: string) => void
  /** Live runs only: re-weight and re-rank through /rescore. */
  tune?: TuneProps
}

/** Sliders under the results header; each change re-ranks after a short pause (no model call). */
function Tune({ spec, featureLabels, busy, onChange }: TuneProps) {
  const [w, setW] = useState<Record<string, number>>(spec.weights)
  const timer = useRef<number | null>(null)
  return (
    <div className="tune">
      <p className="hint">{busy ? 'Re-ranking...' : 'Drag to change what matters; the list and map re-rank with no model call.'}</p>
      <WeightSliders
        weights={w}
        proposed={spec.weights}
        rationale={(spec.rationale ?? {}) as Record<string, string>}
        featureLabels={featureLabels}
        onChange={(next) => {
          setW(next)
          if (timer.current) window.clearTimeout(timer.current)
          timer.current = window.setTimeout(() => onChange(next), 350)
        }}
      />
    </div>
  )
}

/** Ranked final stops. Columns never move between runs; only their values change. */
export function ResultsTable({ report, selectedSiteId, onSelect, tune }: TableProps) {
  const [tuning, setTuning] = useState(false)
  const label = report.demand_label ?? 'patients'
  const recs = report.recommendations
  return (
    <section className="results" aria-label="Ranked sites">
      <header>
        <h2>Top {recs.length} spaces</h2>
        <span className={`verified${report.verified ? '' : ' no'}`}>
          <Icon name={report.verified ? 'check' : 'warn'} />
          {report.verified ? 'Every number checked against its source' : 'Some numbers did not verify'}
        </span>
        {tune && (
          <button className="button" style={{ marginLeft: 'auto' }} aria-expanded={tuning} onClick={() => setTuning((t) => !t)}>
            {tuning ? 'Done tuning' : 'Tune weights'}
          </button>
        )}
      </header>
      {tune && tuning && <Tune {...tune} />}
      <div className="tablewrap">
        <table className="stops">
          <thead>
            <tr>
              <th>Rank</th>
              <th>Space</th>
              <th className="num">{isPatients(label) ? 'Net-new patients a year' : `${label[0].toUpperCase()}${label.slice(1)} in reach`}</th>
              <th className="num">Rent per {singular(label)} won</th>
              <th className="num">Asking rent</th>
              <th className="num">Stays top 10</th>
            </tr>
          </thead>
          <tbody>
            {recs.map((rec, i) => {
              const s = rec.site
              const miss = missesOf(rec)
              const firstMiss = miss.length > 0 && (i === 0 || missesOf(recs[i - 1]).length === 0)
              return (
                <Fragment key={s.id}>
                {firstMiss && (
                  <tr className="looser"><td colSpan={6}>Looser search: these spaces miss one of your constraints</td></tr>
                )}
                <tr
                  className={miss.length ? 'misses' : undefined}
                  aria-selected={s.id === selectedSiteId}
                  tabIndex={0}
                  onClick={() => onSelect(s.id)}
                  onKeyDown={(e) => (e.key === 'Enter' || e.key === ' ') && (e.preventDefault(), onSelect(s.id))}
                >
                  <td>
                    <span className="rankdot">{rec.rank}</span>
                  </td>
                  <td className="addr">
                    {s.address}
                    <span>{[s.sqft ? `${s.sqft.toLocaleString('en-US')} sq ft` : null, s.floor != null ? `floor ${s.floor}` : null, availability(rec).text.toLowerCase(), s.matterport_id ? '3D tour' : null].filter(Boolean).join(', ')}</span>
                    {miss.length > 0 && <span className="miss" title="Misses one of your search constraints"><Icon name="warn" size={12} /> {miss.join(' · ')}</span>}
                  </td>
                  <td className="num won">{whole(rec.net_new_patients)}</td>
                  <td className="num">{rec.annual_rent_per_customer != null ? money(rec.annual_rent_per_customer) : 'Not listed'}</td>
                  <td className="num">{s.rent_psf ? `${money(s.rent_psf)}/sq ft` : 'Not listed'}</td>
                  <td className="num">{rec.stability != null ? `${Math.round(rec.stability * 100)}%` : 'Not run'}</td>
                </tr>
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </div>
    </section>
  )
}

/** Search constraints this space fails (set when a looser search added it to fill the top 10). */
function missesOf(rec: Recommendation): string[] {
  const m = (rec.site.checks as Record<string, unknown> | undefined)?.misses
  return Array.isArray(m) ? (m as string[]) : []
}

interface FlyerProps {
  report: Report
  rec: Recommendation | undefined
  featureLabels: Record<string, string>
  onSelect: (id: string) => void
}

/** The selected site, set the way brokers present space, with Siteline's verified numbers. */
export function SiteFlyer({ report, rec, featureLabels, onSelect }: FlyerProps) {
  if (!rec) return <p className="empty">Select a space in the list or on the map to see its details.</p>
  const s = rec.site
  const label = report.demand_label ?? 'patients'
  const avail = availability(rec)
  const annualRent = s.rent_psf && s.sqft ? s.rent_psf * s.sqft : null
  const checks = (s.checks ?? {}) as Record<string, unknown>
  return (
    <article className="flyer" aria-labelledby="flyer-title">
      <nav className="pager" aria-label="Ranked spaces">
        {report.recommendations.map((r) => (
          <button key={r.site.id} aria-current={r.site.id === s.id} aria-label={`Rank ${r.rank}: ${r.site.address}`} onClick={() => onSelect(r.site.id)}>
            {r.rank}
          </button>
        ))}
      </nav>
      <div className="badges">
        <span className="ranktag">Rank {rec.rank} of {report.recommendations.length}</span>
        <span className={`avail${avail.pending ? ' pending' : ''}`}>{avail.text}</span>
        {missesOf(rec).length > 0 && <span className="misstag" title="Misses one of your search constraints"><Icon name="warn" size={13} />{missesOf(rec).join(' · ')}</span>}
      </div>
      <h2 id="flyer-title">{s.address}</h2>
      <p className="where">
        {[s.submarket, s.building_class ? `Class ${s.building_class} building` : null, s.source === 'costar' ? 'CoStar listing' : 'Example site'].filter(Boolean).join(', ')}
      </p>

      <dl className="facts">
        <div>
          <dt>Size</dt>
          <dd>{s.sqft ? s.sqft.toLocaleString('en-US') : 'n/a'}</dd>
        </div>
        <div>
          <dt>Rent / sq ft / yr</dt>
          <dd>{s.rent_psf ? money(s.rent_psf) : 'n/a'}</dd>
        </div>
        <div>
          <dt>Floor</dt>
          <dd>{s.floor ?? 'n/a'}</dd>
        </div>
        <div>
          <dt>Parking</dt>
          <dd>{s.parking_spaces ?? 'n/a'}</dd>
        </div>
      </dl>
      <p className="srcline" style={{ marginTop: -12, marginBottom: 18 }}>
        Size, rent, floor and parking: <b>{s.source === 'costar' ? 'CoStar listing' : 'example site'}</b>
      </p>

      <div className="won-block">
        <div className="n">{whole(rec.net_new_patients)}</div>
        <p className="l">{isPatients(label) ? 'net-new patients a year, after competitors and your other locations take their share' : `${label} in reach would pick this site over competitors and your other locations`}</p>
        <p className="srcline">
          Computed by <b>Siteline's Huff model</b> from the data sources; verified before display.
        </p>
      </div>

      <table className="ringtable">
        <thead>
          <tr>
            <th>What this space would win and cost</th>
            <th>Value</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>Rent per {singular(label)} won, a year</td>
            <td>{rec.annual_rent_per_customer != null ? money(rec.annual_rent_per_customer) : 'Rent not listed'}</td>
          </tr>
          <tr>
            <td>Annual asking rent</td>
            <td>{annualRent ? `$${whole(annualRent)}` : 'Not listed'}</td>
          </tr>
          <tr>
            <td>Zone fit for this business</td>
            <td>{Math.round(rec.zone_score * 100)} / 100</td>
          </tr>
          <tr>
            <td>Space quality (size, floor, parking, rent)</td>
            <td>{Math.round(rec.site_quality * 100)} / 100</td>
          </tr>
          <tr>
            <td>Final score</td>
            <td>{Math.round(rec.final_score * 100)} / 100</td>
          </tr>
          <tr>
            <td>Stays in the top 10 when weights shift</td>
            <td>{rec.stability != null ? `${Math.round(rec.stability * 100)}% of 200 tries` : 'Not run'}</td>
          </tr>
        </tbody>
      </table>

      <h3>Why it ranks here</h3>
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
        <>
          <h3>Keep in mind</h3>
          {(rec.caveats ?? []).map((c) => (
            <p className="caveat" key={c}>
              {c}
            </p>
          ))}
        </>
      )}

      {rec.inspection && <Inspection insp={rec.inspection} />}
      {rec.community && <Community need={rec.community} />}
      <Aerial key={s.id} rec={rec} />
      <TourDetails key={`t-${s.id}`} id={s.matterport_id ?? null} />
      {checks.listing_type != null && (
        <p className="srcline" style={{ marginTop: 14 }}>
          Listing: <b>{String(checks.listing_type).replace(/([a-z])([A-Z])/g, '$1 $2')}</b>
          {checks.on_market_since ? `, on the market since ${String(checks.on_market_since)}` : ''}
        </p>
      )}

      <div className="actions">
        {s.matterport_id && (
          <a className="button primary" href={`https://my.matterport.com/show/?m=${encodeURIComponent(s.matterport_id)}`} target="_blank" rel="noreferrer">
            <Icon name="cube" />
            Open the 3D tour
          </a>
        )}
      </div>
    </article>
  )
}

const AERIAL_M = 160 // metres across the aerial image

/** Vexcel aerial of the building with parked-car detection (only when the dev server has a Vexcel key). */
function Aerial({ rec }: { rec: Recommendation }) {
  const s = rec.site
  const [cars, setCars] = useState<CarCount | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const img = HAS_VEXCEL ? aerialUrl(s.lat, s.lon, AERIAL_M) : null
  if (!img) return null
  const mLat = 111320
  const mLon = 111320 * Math.cos((s.lat * Math.PI) / 180)
  const pct = (lon: number, lat: number) => [50 + (((lon - s.lon) * mLon) / AERIAL_M) * 100, 50 - (((lat - s.lat) * mLat) / AERIAL_M) * 100]
  const count = async () => {
    setBusy(true)
    setErr(null)
    try {
      setCars(await countParkedCars(s.lat, s.lon))
    } catch (e) {
      setErr((e as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      <h3>From above</h3>
      <figure className="aerial">
        <img src={img} alt={`Aerial view of ${s.address}`} />
        {cars && (
          <svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
            {cars.boxes.map((ring, i) => (
              <polygon key={i} points={ring.map(([x, y]) => pct(x, y).join(',')).join(' ')} />
            ))}
          </svg>
        )}
      </figure>
      <div className="aerial-row">
        <button className="button" disabled={busy} onClick={count}>
          {busy ? 'Counting...' : cars ? 'Count again' : 'Count parked cars'}
        </button>
        <span className="srcline">
          {cars ? (
            <>
              <b>{cars.cars} parked cars</b> within {cars.meters / 2} m (Vexcel detection); the listing says {s.parking_spaces ?? 'nothing about'} spaces.
            </>
          ) : (
            <>Aerial: <b>Vexcel</b></>
          )}
        </span>
      </div>
      {err && <p className="caveat">{err}</p>}
    </>
  )
}

/** Matterport model details (name, scanned area, rooms) when the dev server has Matterport API keys. */
function TourDetails({ id }: { id: string | null }) {
  const [scan, setScan] = useState<ScanInfo | null>(null)
  useEffect(() => {
    if (id) matterportScan(id).then(setScan).catch(() => setScan(null))
  }, [id])
  if (!scan?.name) return null
  return (
    <p className="srcline" style={{ marginTop: 14 }}>
      3D scan: <b>{scan.name}</b>
      {scan.areaSqft ? `, ${scan.areaSqft.toLocaleString('en-US')} sq ft scanned` : ''}
      {scan.rooms ? `, ${scan.rooms} rooms` : ''} (Matterport)
    </p>
  )
}

const pct = (v: number | null | undefined) => (v == null ? 'n/a' : `${Math.round(v * 100)}%`)

const CHECKS: [string, string][] = [
  ['listing', 'CoStar listing'],
  ['aerial', 'Vexcel aerial'],
  ['3d', 'Matterport 3D scan'],
]

/** What the site-inspector checked: listing, aerial and 3D scan, with the numbers each layer gave. */
function Inspection({ insp }: { insp: SiteInspection }) {
  const done = new Set(insp.checked ?? [])
  const why = (key: string) => (insp.unavailable ?? []).find((u) => u.startsWith(`${key}:`))?.split(': ').slice(1).join(': ')
  const occ = insp.occupancy ?? null
  return (
    <section className="inspect" aria-label="Site inspection">
      <h3>Inspected from above and inside</h3>
      <ul className="checks">
        {CHECKS.map(([key, label]) => (
          <li key={key} data-done={done.has(key)}>
            <span className="tick" aria-hidden="true">{done.has(key) ? <Icon name="check" size={14} /> : null}</span>
            <span>
              {label}
              {!done.has(key) && <small>{why(key) ?? 'not checked for this space'}</small>}
            </span>
          </li>
        ))}
      </ul>
      {insp.parked_cars != null && (
        <div className="meter-row">
          <div className="meter-head">
            <span>Parking at the time of the aerial</span>
            <b>
              {insp.parked_cars} cars{insp.listed_parking ? ` / ${insp.listed_parking} spaces` : ''}
            </b>
          </div>
          {occ != null && (
            <div className="occ" data-tight={occ >= 0.85} aria-hidden="true">
              <span style={{ width: `${Math.min(100, occ * 100)}%` }} />
            </div>
          )}
          <p className="srcline">
            Vexcel parked-car detection within {insp.radius_m} m{occ != null ? `, ${pct(occ)} full` : ''}; listed spaces from CoStar.
          </p>
        </div>
      )}
      {(insp.scan_area_sqft != null || insp.scan_rooms != null) && (
        <p className="srcline" style={{ marginTop: 8 }}>
          3D scan{insp.scan_name ? ` "${insp.scan_name}"` : ''}:{' '}
          <b>
            {[insp.scan_area_sqft != null ? `${insp.scan_area_sqft.toLocaleString('en-US')} sq ft indoors` : null, insp.scan_floors ? `${insp.scan_floors} floors` : null, insp.scan_rooms ? `${insp.scan_rooms} rooms` : null]
              .filter(Boolean)
              .join(', ')}
          </b>{' '}
          (Matterport)
        </p>
      )}
    </section>
  )
}

/** Who lives within the catchment, from Census data: the community-need lens. */
function Community({ need }: { need: CommunityNeed }) {
  const rows: [string, number | null | undefined][] = [
    ['Households without a car', need.no_car_share],
    ['Households in lower-income tracts', need.lower_income_share],
    ['Renter households', need.renter_share],
    ['Residents with Medicaid', need.medicaid_share],
  ]
  return (
    <section className="community" aria-label="Who lives within reach">
      <h3>Who lives within {Math.round(need.minutes)} minutes</h3>
      <dl className="shares">
        {rows
          .filter(([, v]) => v != null)
          .map(([label, v]) => (
            <div key={label}>
              <dt>{label}</dt>
              <dd>
                <span className="sharebar" aria-hidden="true">
                  <span style={{ width: `${Math.min(100, (v ?? 0) * 100)}%` }} />
                </span>
                <b>{pct(v)}</b>
              </dd>
            </div>
          ))}
      </dl>
      <p className="srcline">
        {need.households != null ? `${need.households.toLocaleString('en-US')} households` : 'Households'} in reach. Census ACS 2024; lower-income means a tract median under 80% of the region's.
      </p>
    </section>
  )
}
