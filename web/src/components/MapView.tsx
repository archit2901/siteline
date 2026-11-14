import { MapboxOverlay } from '@deck.gl/mapbox'
import { H3HexagonLayer } from '@deck.gl/geo-layers'
import { ScatterplotLayer, TextLayer } from '@deck.gl/layers'
import type { PickingInfo } from '@deck.gl/core'
import * as maplibregl from 'maplibre-gl'
import { useEffect, useMemo, useRef } from 'react'
import { esc } from '../lib/safe'
import type { Office, Recommendation, ScoreResponse } from '../types/contracts'

// Fixture/grid row (fixtures/grid.json): not a contract shape.
export interface GridCell {
  h3: string
  cell_lat: number
  cell_lon: number
  county: string
  [column: string]: string | number | null
}

type Zone = ScoreResponse['zones'][number]
type RGBA = [number, number, number, number]

const BASEMAP = 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json'
const RAMP: [number, number, number][] = [[224, 246, 97], [196, 240, 65], [170, 194, 170], [95, 138, 106], [35, 74, 51]]
const CUT = 0.7 // with a score, only the top 30% of cells are coloured so the basemap stays readable

function ramp(t: number, cut: number, alphaMin: number, alphaMax: number): RGBA {
  if (t < cut) return [0, 0, 0, 0]
  const x = ((t - cut) / (1 - cut)) * (RAMP.length - 1)
  const i = Math.min(RAMP.length - 2, Math.floor(x))
  const f = x - i
  const c = [0, 1, 2].map((k) => Math.round(RAMP[i][k] + (RAMP[i + 1][k] - RAMP[i][k]) * f))
  return [c[0], c[1], c[2], Math.round(alphaMin + (alphaMax - alphaMin) * ((t - cut) / (1 - cut)))]
}

/** Percentile rank in [0, 1] for each value (missing -> null). */
function percentiles(values: (number | null)[]): (number | null)[] {
  const idx = values.map((v, i) => [v, i] as const).filter(([v]) => v !== null && Number.isFinite(v))
  idx.sort((a, b) => (a[0] as number) - (b[0] as number))
  const out: (number | null)[] = values.map(() => null)
  idx.forEach(([, i], rank) => { out[i] = idx.length > 1 ? rank / (idx.length - 1) : 1 })
  return out
}

const fmt = (v: unknown, digits = 0) => (typeof v === 'number' ? v.toLocaleString('en-US', { maximumFractionDigits: digits }) : 'no data')

interface Props {
  grid: GridCell[]
  zones: ScoreResponse | null
  sites: Recommendation[]
  highlight: string[] // h3 cells to outline (e.g. top zones in the Build tab)
  offices: Office[]
  featureLabels: Record<string, string>
  selectedSiteId: string | null
  onSelectSite: (id: string) => void
  focus: { lat: number; lon: number; zoom?: number } | null
}

export default function MapView({ grid, zones, sites, highlight, offices, featureLabels, selectedSiteId, onSelectSite, focus }: Props) {
  const container = useRef<HTMLDivElement>(null)
  const map = useRef<maplibregl.Map | null>(null)
  const overlay = useRef<MapboxOverlay | null>(null)

  useEffect(() => {
    if (!container.current) return
    const m = new maplibregl.Map({ container: container.current, style: BASEMAP, center: [-95.37, 29.76], zoom: 9, attributionControl: { compact: true } })
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'bottom-right')
    const o = new MapboxOverlay({ interleaved: false, layers: [] })
    m.addControl(o)
    map.current = m
    overlay.current = o
    // The card changes width between layouts without a window resize; keep the canvas in sync.
    const ro = new ResizeObserver(() => m.resize())
    ro.observe(container.current)
    return () => { ro.disconnect(); m.remove(); map.current = null; overlay.current = null }
  }, [])

  const densityPct = useMemo(() => percentiles(grid.map((c) => c.pop_density_km2 as number | null)), [grid])
  const scorePct = useMemo(() => {
    if (!zones) return null
    const pct = percentiles(zones.zones.map((z) => z.score))
    return new Map(zones.zones.map((z, i) => [z.h3, pct[i] ?? 0]))
  }, [zones])
  const zoneByCell = useMemo(() => new Map((zones?.zones ?? []).map((z) => [z.h3, z])), [zones])

  useEffect(() => {
    const o = overlay.current
    if (!o) return
    const cellIndex = new Map(grid.map((c, i) => [c.h3, i]))
    const pinned = offices.filter((x) => x.lat != null && x.lon != null)
    const layers = [
      new H3HexagonLayer<GridCell>({
        id: 'cells', data: grid, getHexagon: (d) => d.h3, stroked: false, extruded: false, pickable: true, highPrecision: false,
        getFillColor: (d) => (scorePct
          ? ramp(scorePct.get(d.h3) ?? 0, CUT, 95, 190)
          : ramp(densityPct[cellIndex.get(d.h3) ?? 0] ?? 0, 0.5, 25, 110)),
        updateTriggers: { getFillColor: [scorePct, densityPct] },
      }),
      new H3HexagonLayer<string>({
        id: 'highlight', data: highlight, getHexagon: (d) => d, filled: true, stroked: true,
        getFillColor: [250, 142, 102, 70], getLineColor: [0, 0, 0, 255], lineWidthMinPixels: 1.5,
      }),
      new ScatterplotLayer<typeof pinned[number]>({
        id: 'offices', data: pinned, getPosition: (d) => [d.lon as number, d.lat as number], getRadius: 7, radiusUnits: 'pixels',
        getFillColor: [188, 230, 250, 255], getLineColor: [0, 0, 0, 255], lineWidthMinPixels: 2, stroked: true,
      }),
      new ScatterplotLayer<Recommendation>({
        id: 'sites', data: sites, getPosition: (d) => [d.site.lon, d.site.lat], radiusUnits: 'pixels',
        getRadius: (d) => (d.site.id === selectedSiteId ? 15 : 12),
        getFillColor: (d) => (d.site.id === selectedSiteId ? [196, 240, 65, 255] : [0, 0, 0, 255]),
        getLineColor: [255, 255, 255, 255], lineWidthMinPixels: 2, stroked: true, pickable: true,
        onClick: (info) => info.object && onSelectSite(info.object.site.id),
        updateTriggers: { getRadius: selectedSiteId, getFillColor: selectedSiteId },
      }),
      new TextLayer<Recommendation>({
        id: 'site-ranks', data: sites, getPosition: (d) => [d.site.lon, d.site.lat], getText: (d) => String(d.rank), getSize: 12,
        getColor: (d) => (d.site.id === selectedSiteId ? [0, 0, 0, 255] : [255, 255, 255, 255]), fontFamily: 'Geist, system-ui, sans-serif',
        fontWeight: 600, updateTriggers: { getColor: selectedSiteId },
      }),
    ]
    o.setProps({
      layers,
      getTooltip: ({ object, layer }: PickingInfo) => {
        const style = { fontFamily: 'Geist, system-ui, sans-serif', fontSize: '12px', background: '#000', color: '#fff', borderRadius: '8px', padding: '7px 10px', maxWidth: '280px' }
        if (object && layer?.id === 'sites') return { html: `<b>#${esc((object as Recommendation).rank)}</b> ${esc((object as Recommendation).site.address)}`, style }
        if (!object || layer?.id !== 'cells') return null
        const cell = object as GridCell
        const zone: Zone | undefined = zoneByCell.get(cell.h3)
        if (zone) {
          const top = Object.entries(zone.contributions ?? {}).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([k]) => featureLabels[k] ?? k)
          return { html: `<b>Fit ${esc(fmt(zone.score, 2))}</b> · ${esc(cell.county)} County<br/>Expected demand ${esc(fmt(zone.demand))} a year<br/>Strongest: ${top.map(esc).join(', ')}`, style }
        }
        return { html: `<b>${esc(cell.county)} County</b><br/>${esc(fmt(cell.pop_density_km2))} people per km²<br/>${esc(fmt(cell.pop_total_within_5km))} people within 5 km`, style }
      },
    })
  }, [grid, densityPct, scorePct, zoneByCell, highlight, offices, sites, selectedSiteId, featureLabels, onSelectSite])

  useEffect(() => {
    if (!map.current || grid.length === 0) return
    const lats = grid.map((c) => c.cell_lat)
    const lons = grid.map((c) => c.cell_lon)
    map.current.fitBounds([[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]], { padding: 40, duration: 0 })
  }, [grid])

  useEffect(() => {
    if (!map.current || !sites.length) return
    const b = new maplibregl.LngLatBounds()
    sites.forEach((s) => b.extend([s.site.lon, s.site.lat]))
    map.current.fitBounds(b, { padding: 90, maxZoom: 11.5, duration: 800 })
  }, [sites.map((s) => s.site.id).join(',')]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (focus && map.current) map.current.flyTo({ center: [focus.lon, focus.lat], zoom: focus.zoom ?? 12.5, duration: 900 })
  }, [focus])

  return (
    <>
      <div ref={container} className="map" aria-label="Map of Houston scored by H3 hexagon" />
      <div className="legend">
        <span>{zones ? 'Fit for this business' : 'People per km²'}</span><span className="ramp" />
        <span className="mono">{zones ? 'top 30% → 1%' : 'lower → higher'}</span>
        {sites.length > 0 && <span><i style={{ background: '#000' }} />site</span>}
        {offices.some((x) => x.lat != null) && <span><i style={{ background: 'var(--sky)', border: '1.5px solid #000' }} />your office</span>}
        {highlight.length > 0 && <span><i style={{ background: 'rgba(250,142,102,.5)', border: '1.5px solid #000', borderRadius: 2 }} />zone</span>}
      </div>
    </>
  )
}
