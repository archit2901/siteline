import { cellToLatLng, gridDistance } from 'h3-js'
import { aerialUrl } from '../lib/media'
import { safeHref } from '../lib/safe'
import { useEffect, useRef, useState } from 'react'
import type { Recommendation, Report, ScoreResponse, ScoringSpec } from '../types/contracts'
import type { GridCell } from './MapView'
import SourceLink from './SourceLink'

const COLORS = ['var(--sky)', 'var(--lime)', 'var(--sand)', 'var(--sage)', 'var(--coral)', 'var(--steel)', 'var(--lime2)']
/** "patients" -> "new patients/yr"; general businesses count customers in reach. */
const demandShort = (label?: string) => `new ${label && label !== 'patients' ? label : 'pts'}/yr`
const k = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : `${Math.round(n)}`)

export function SiteCard({ r, report, featureLabels, active, onHover, onOpen }: {
  r: Recommendation; report: Report; featureLabels: Record<string, string>; active: boolean; onHover: (id: string | null) => void; onOpen: () => void
}) {
  const s = r.site
  const img = aerialUrl(s.lat, s.lon, 140)
  const name = (s.checks?.building_name as string | undefined) ?? s.submarket ?? (s.source === 'costar' ? 'CoStar listing' : 'Example site')
  return (
    <article className={`scard ${active ? 'active' : ''}`} onMouseEnter={() => onHover(s.id)} onMouseLeave={() => onHover(null)} onClick={onOpen}
      tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && onOpen()}>
      <div className="l">
        <span className="num">{r.rank}/10</span>
        <h3>{s.address.split(',')[0]}</h3>
        <span className="meta">{name}{s.building_class ? ` · Class ${s.building_class}` : ''}{s.available === null || s.available === undefined ? ' · candidate building' : ''}</span>
        <div className="stats">
          <div><b>{k(r.net_new_patients)}</b><span>{demandShort(report.demand_label)}</span></div>
          <div><b>{s.sqft ? k(s.sqft) : '–'}</b><span>sq ft</span></div>
          <div><b>{s.rent_psf ? `$${Math.round(s.rent_psf)}` : '–'}</b><span>per sf/yr</span></div>
        </div>
        {r.annual_rent_per_customer != null && (
          <span className="meta mono">${r.annual_rent_per_customer.toFixed(2)} rent per {(report.demand_label ?? 'patients').replace(/s$/, '')} a year</span>
        )}
        <ul className="bul">
          {r.justification.slice(1, 4).map((c, i) => (
            <li key={i}><span>{c.text}{' '}{c.evidence_refs.slice(0, 1).map((ref) => <SourceLink key={ref} evidenceRef={ref} report={report} featureLabels={featureLabels} />)}</span></li>
          ))}
        </ul>
      </div>
      <div className="r" style={{ background: COLORS[(r.rank - 1) % COLORS.length] }}>
        <div className="img" style={img ? { backgroundImage: `url(${img})` } : undefined}>{!img && <span className="noimg">Aerial needs a Vexcel key</span>}</div>
        {r.stability != null && <span className="badge">top 10 in {Math.round(r.stability * 100)}%</span>}
        {s.matterport_id && <span className="badge2">3D</span>}
      </div>
    </article>
  )
}

export interface TopZone {
  rank: number
  h3: string
  lat: number
  lon: number
  score: number
  demand: number
  county: string
  position: number
  of: number
  contributions: [string, number][]
}

/** The run's top zones (ScoreResponse.top, already ranked by the model), skipping cells next to a better one. */
export function topZones(zones: ScoreResponse, grid: GridCell[], n = 6): TopZone[] {
  const byCell = new Map(zones.zones.map((z) => [z.h3, z]))
  const position = new Map(zones.zones.map((z, i) => [z.h3, i + 1]))
  const county = new Map(grid.map((c) => [c.h3, c.county]))
  const picked: string[] = []
  for (const h of zones.top) {
    if (picked.every((p) => { try { return gridDistance(p, h) > 3 } catch { return true } })) picked.push(h)
    if (picked.length >= n) break
  }
  return picked.map((h, i) => {
    const z = byCell.get(h)!
    const [lat, lon] = cellToLatLng(h)
    return { rank: i + 1, h3: h, lat, lon, score: z.score, demand: z.demand, county: county.get(h) ?? 'Houston', position: position.get(h) ?? 0, of: zones.zones.length,
      contributions: Object.entries(z.contributions ?? {}).sort((a, b) => b[1] - a[1]).slice(0, 3) }
  })
}

export function ZoneCard({ z, total, featureLabels, onFocus }: { z: TopZone; total: number; featureLabels: Record<string, string>; onFocus: () => void }) {
  const img = aerialUrl(z.lat, z.lon, 900)
  return (
    <article className="scard" onClick={onFocus} tabIndex={0} onKeyDown={(e) => e.key === 'Enter' && onFocus()}>
      <div className="l">
        <span className="num">{z.rank}/{total}</span>
        <h3>{z.county} County</h3>
        <span className="meta mono">{z.h3}</span>
        <div className="stats">
          <div><b>{z.score.toFixed(2)}</b><span>fit score</span></div>
          <div><b>{k(z.demand)}</b><span>demand/yr</span></div>
          <div><b>#{z.position}</b><span>of {z.of.toLocaleString()} cells</span></div>
        </div>
        <ul className="bul">{z.contributions.map(([fid, c]) => <li key={fid}><span>{featureLabels[fid] ?? fid} · {(c * 100).toFixed(0)} pts of fit</span></li>)}</ul>
      </div>
      <div className="r" style={{ background: COLORS[(z.rank + 2) % COLORS.length] }}>
        <div className="img" style={img ? { backgroundImage: `url(${img})` } : undefined}>{!img && <span className="noimg">Aerial needs a Vexcel key</span>}</div>
        <span className="badge">build-to-suit zone</span>
      </div>
    </article>
  )
}

export function Proof({ report, spec }: { report: Report; spec: ScoringSpec | null }) {
  const claims = report.recommendations.reduce((n, r) => n + r.justification.length, 0)
  const found = report.factors.filter((f) => f.status === 'found')
  return (
    <div>
      <div className="callout">
        {report.verified
          ? <><b>Verified.</b> The analyst-critic matched every number in all {claims} claims to a stored value or model output.</>
          : <><b>Not verified yet.</b> The analyst-critic has not confirmed the numbers in these {claims} claims.</>}
        {' '}<b>{found.length}</b> of {report.factors.length} factors use data the agent found online.
      </div>
      <div className="kicker"><span>Factors</span><span>status · source</span></div>
      {report.factors.map((f) => {
        const href = f.feature_id ? safeHref(report.sources?.[f.feature_id]) : undefined
        return (
          <div className="ev-row" key={f.id}>
            <span>{f.factor} <span className={`lyr lyr-${f.status === 'found' ? 'agent_found' : 'stock'}`}>{f.status}</span>
              {f.caveat && <em className="warn"> · {f.caveat}</em>}</span>
            <span>{href ? <a className="source" href={href} target="_blank" rel="noreferrer" title={href}>{href.includes('/NummVBqZSIJKUeVR/') ? 'City of Houston' : new URL(href).hostname.replace(/^www\./, '')}</a> : <em>no source link</em>}</span>
          </div>
        )
      })}
      {spec && (
        <>
          <div className="kicker" style={{ marginTop: 26 }}><span>Why each factor got its weight</span><span>personalized</span></div>
          {Object.entries(spec.weights).sort((a, b) => b[1] - a[1]).map(([fid, w]) => (
            <div className="ev-row" key={fid}>
              <span><b style={{ fontWeight: 500 }}>{report.factors.find((f) => f.feature_id === fid)?.factor ?? fid}</b>
                {(spec.rationale as Record<string, string> | undefined)?.[fid] && <em className="dim-why"> · {(spec.rationale as Record<string, string>)[fid]}</em>}</span>
              <span className="mono">{Math.round(w * 100)}%</span>
            </div>
          ))}
        </>
      )}
      <details className="caveats">
        <summary>Model caveats</summary>
        <ul>{(report.caveats ?? []).map((c, i) => <li key={i}>{c}</li>)}</ul>
      </details>
    </div>
  )
}

/** Re-weight after the run (live runs only): POST /runs/{id}/rescore re-ranks with no model call. */
export function Tune({ spec, featureLabels, busy, onChange }: {
  spec: ScoringSpec; featureLabels: Record<string, string>; busy: boolean; onChange: (w: Record<string, number>) => void
}) {
  const [open, setOpen] = useState(false)
  const [w, setW] = useState<Record<string, number>>(spec.weights)
  useEffect(() => setW(spec.weights), [spec])
  const t = useRef<number | null>(null)
  const total = Object.values(w).reduce((a, b) => a + b, 0) || 1
  const top = Math.max(0.5, ...Object.values(spec.weights).map((x) => Math.min(1, x * 2.5)))
  if (!open) return <button className="pillbtn" style={{ marginTop: 10 }} onClick={() => setOpen(true)}>Tune weights{busy ? ' …' : ''}</button>
  return (
    <div className="callout" style={{ padding: '12px 18px' }}>
      <div className="kicker" style={{ margin: '0 0 6px' }}><span>Tune weights · re-ranks instantly</span>
        <button className="linkbtn" style={{ margin: 0 }} onClick={() => setOpen(false)}>close</button></div>
      {Object.keys(spec.weights).map((fid) => (
        <div className="wrow edit" key={fid}>
          <span title={featureLabels[fid] ?? fid}>{featureLabels[fid] ?? fid}</span>
          <input type="range" min={0} max={top} step={0.005} value={w[fid] ?? 0} aria-label={`Weight for ${featureLabels[fid] ?? fid}`}
            onChange={(e) => {
              const next = { ...w, [fid]: Number(e.target.value) }
              setW(next)
              if (t.current) clearTimeout(t.current)
              t.current = window.setTimeout(() => onChange(next), 300)
            }} />
          <span className="pct">{Math.round(((w[fid] ?? 0) / total) * 100)}%</span>
        </div>
      ))}
    </div>
  )
}
