import { useEffect, useState } from 'react'
import { HAS_VEXCEL, aerialUrl, countParkedCars, matterportScan, type CarCount, type ScanInfo } from '../lib/media'
import type { Recommendation, Report } from '../types/contracts'
import SourceLink from './SourceLink'

type Tab = 'aerial' | '3d'
const M = 160 // metres across the aerial image

/** Everything Siteline knows about one recommended space: CoStar listing fields (SiteCandidate + checks), the claims
 *  with their sources, a Vexcel aerial with parked-car detection, and the Matterport tour when the building has one. */
export default function PropertySheet({ rec, report, featureLabels, onClose }: {
  rec: Recommendation; report: Report; featureLabels: Record<string, string>; onClose: () => void
}) {
  const s = rec.site
  const c = (s.checks ?? {}) as Record<string, unknown>
  const [tab, setTab] = useState<Tab>(HAS_VEXCEL || !s.matterport_id ? 'aerial' : '3d')
  const [cars, setCars] = useState<CarCount | null>(null)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [scan, setScan] = useState<ScanInfo | null>(null)

  useEffect(() => { if (tab === '3d' && s.matterport_id && !scan) matterportScan(s.matterport_id).then(setScan) }, [tab, s.matterport_id, scan])
  useEffect(() => {
    const key = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', key)
    return () => window.removeEventListener('keydown', key)
  }, [onClose])

  const count = async () => {
    setBusy(true); setErr(null)
    try { setCars(await countParkedCars(s.lat, s.lon)) } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  // CoStar enum words ("FirstFloor", "ShoppingCenterDirectSpaceForLease") read better with spaces
  const words = (x: unknown) => (typeof x === 'string' ? x.replace(/([a-z])([A-Z])/g, '$1 $2') : x)
  const v = (x: unknown, suffix = '') => (x === null || x === undefined || x === '' ? '–' : `${typeof x === 'number' ? x.toLocaleString() : String(x)}${suffix}`)
  const ratio = (c.parking_ratio_per_1000sf as number | undefined) ?? (s.parking_spaces && s.sqft ? s.parking_spaces / (s.sqft / 1000) : undefined)
  const img = aerialUrl(s.lat, s.lon, M)
  const mPerLat = 111320
  const mPerLon = 111320 * Math.cos((s.lat * Math.PI) / 180)
  const toPct = (lon: number, lat: number) => [50 + (((lon - s.lon) * mPerLon) / M) * 100, 50 - (((lat - s.lat) * mPerLat) / M) * 100]

  return (
    <div className="sheet-bg" onClick={onClose}>
      <div className="sheet" role="dialog" aria-modal="true" aria-labelledby="sheet-title" onClick={(e) => e.stopPropagation()}>
        <div className="info">
          <span className="hand">{rec.rank}/10</span>
          <h2 id="sheet-title">{s.address}{c.suite ? `, Suite ${c.suite as string}` : ''}</h2>
          <div className="sub">{v(c.building_name ?? s.submarket)} · {s.source === 'costar' ? 'CoStar listing' : 'Example site (fixture data)'}</div>
          <div className="stats" style={{ marginTop: 18 }}>
            <div><b>{Math.round(rec.net_new_patients).toLocaleString()}</b><span>new pts / yr</span></div>
            <div><b>{rec.stability == null ? '–' : `${Math.round(rec.stability * 100)}%`}</b><span>stability</span></div>
            <div><b>{v(s.sqft)}</b><span>sq ft</span></div>
          </div>
          <div className="facts">
            <div><span>Asking rent</span>{s.rent_psf ? `$${s.rent_psf.toFixed(2)}${c.rent_high_psf ? `–${(c.rent_high_psf as number).toFixed(2)}` : ''}/sf/yr` : '–'}</div>
            <div><span>Floor</span>{v(words(c.floor_label ?? s.floor))}</div>
            <div><span>Space use</span>{v(words(c.space_use))}</div>
            <div><span>Class</span>{v(s.building_class)}</div>
            <div><span>Parking</span>{v(s.parking_spaces)}{ratio ? ` (${ratio.toFixed(2)}/1k sf)` : ''}{s.parking_estimated ? ' est.' : ''}</div>
            <div><span>Availability</span>{s.available === true ? 'For lease' : s.available === false ? 'Lease pending' : 'Candidate building'}</div>
            <div><span>Listing</span>{v(words(c.listing_type))}</div>
            <div><span>On market since</span>{v(c.on_market_since)}</div>
            <div><span>Lease type</span>{v(words(c.service_type))}</div>
            <div><span>Distance to zone</span>{c.distance_to_zone_km != null ? `${v(c.distance_to_zone_km)} km` : '–'}</div>
          </div>
          <div className="kicker"><span>Why this site</span><span>{report.verified ? 'every number verified' : 'not verified yet'}</span></div>
          <ul className="bul">
            {rec.justification.map((cl, i) => (
              <li key={i}><span>{cl.text}{' '}{cl.evidence_refs.map((ref) => <SourceLink key={ref} evidenceRef={ref} report={report} featureLabels={featureLabels} />)}</span></li>
            ))}
          </ul>
          {(rec.caveats ?? []).length > 0 && <p className="warn">{(rec.caveats ?? []).join(' · ')}</p>}
          <div className="kicker"><span>Model</span></div>
          <div className="facts">
            <div><span>Zone score</span>{rec.zone_score.toFixed(3)}</div>
            <div><span>Site quality</span>{rec.site_quality.toFixed(3)}</div>
            <div><span>Final score</span>{rec.final_score.toFixed(3)}</div>
            <div><span>Scans</span>{v(c.n_matterport_models ?? (s.matterport_id ? 1 : 0))}</div>
          </div>
          {err && <div className="err">{err}</div>}
        </div>

        <div className="media">
          <div className="mtabs">
            <button className={`pillbtn ${tab === 'aerial' ? 'dark' : ''}`} onClick={() => setTab('aerial')}>Aerial · Vexcel</button>
            <button className={`pillbtn ${tab === '3d' ? 'dark' : ''}`} disabled={!s.matterport_id} onClick={() => setTab('3d')}>3D · Matterport</button>
            {tab === 'aerial' && HAS_VEXCEL && (
              <button className="pillbtn" style={{ marginLeft: 'auto' }} disabled={busy} onClick={count}>
                {busy ? <i className="spin" /> : cars ? `${cars.cars} cars detected` : 'Count parked cars'}</button>
            )}
            <button className="pillbtn dark" style={{ marginLeft: tab === 'aerial' && HAS_VEXCEL ? 0 : 'auto' }} onClick={onClose}>Close</button>
          </div>
          <div className="viewer">
            {tab === 'aerial' && (img
              ? <>
                  <img className="full" src={img} alt={`Aerial view of ${s.address}`} />
                  {cars && (
                    <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="boxes" aria-hidden="true">
                      {cars.boxes.map((ring, i) => <polygon key={i} points={ring.map(([x, y]) => toPct(x, y).join(',')).join(' ')} fill="none" stroke="#c4f041" strokeWidth=".45" />)}
                    </svg>
                  )}
                  {cars && <span className="overlay-note">{cars.cars} parked cars within {cars.meters / 2} m · listing says {v(s.parking_spaces)} spaces</span>}
                </>
              : <div className="empty">Add VEXCEL_API_KEY to web/.env.local to see the aerial.</div>)}
            {tab === '3d' && (scan
              ? <iframe src={scan.embed} title="Matterport 3D tour" allow="xr-spatial-tracking; fullscreen" allowFullScreen />
              : <div className="empty"><i className="spin" /></div>)}
            {tab === '3d' && scan?.name && <span className="overlay-note">{scan.name}{scan.areaSqft ? ` · ${scan.areaSqft.toLocaleString()} sf scanned` : ''}{scan.rooms ? ` · ${scan.rooms} rooms` : ''}</span>}
          </div>
        </div>
      </div>
    </div>
  )
}
