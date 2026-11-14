import { MapboxOverlay } from '@deck.gl/mapbox'
import { H3HexagonLayer } from '@deck.gl/geo-layers'
import { ScatterplotLayer, TextLayer } from '@deck.gl/layers'
import type { PickingInfo } from '@deck.gl/core'
import * as maplibregl from 'maplibre-gl'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { esc } from '../lib/safe'
import type { Recommendation, ScoreResponse } from '../types/contracts'

// Fixture/grid row (fixtures/grid.json): not a contract shape.
export interface GridCell {
  h3: string
  cell_lat: number
  cell_lon: number
  county: string
  [column: string]: string | number | null
}

type Zone = ScoreResponse['zones'][number]
type RGB = [number, number, number]

const BASEMAP = 'https://basemaps.cartocdn.com/gl/positron-gl-style/style.json'
// Fit for this business (route green) vs. population before a score exists (neutral map gray).
const FIT_RAMP: RGB[] = [
  [0xe9, 0xf4, 0xf0],
  [0x7c, 0xc2, 0xae],
  [0x0e, 0x7c, 0x66],
  [0x06, 0x4d, 0x3f],
]
const POP_RAMP: RGB[] = [
  [0xf1, 0xf0, 0xeb],
  [0xc9, 0xc6, 0xbb],
  [0x8b, 0x88, 0x80],
  [0x4a, 0x48, 0x42],
]
const PICK: RGB = [0xf2, 0xb7, 0x05]
const INK: RGB = [0x15, 0x17, 0x1c]
// Huff catchment by trip type: straight-line km reached in the trip's minutes at 35 mph with a 1.3 detour factor.
const TRIP_MINUTES: Record<string, number> = { neighborhood: 10, local: 20, destination: 30 }
const kmForMinutes = (m: number) => (m / 60) * 35 * 1.609 / 1.3
/** Hex fill fades as the map zooms in, so streets show where sites are compared (full at city scale, light up close). */
const fadeForZoom = (z: number) => (z <= 9.5 ? 1 : z >= 11.5 ? 0.3 : 1 - ((z - 9.5) / 2) * 0.7)

function ramp(stops: RGB[], t: number): RGB {
  const x = Math.min(1, Math.max(0, t)) * (stops.length - 1)
  const i = Math.min(stops.length - 2, Math.floor(x))
  const f = x - i
  return [0, 1, 2].map((k) => Math.round(stops[i][k] + (stops[i + 1][k] - stops[i][k]) * f)) as RGB
}

/** Percentile rank in [0, 1] for each value (missing -> null). */
function percentiles(values: (number | null)[]): (number | null)[] {
  const idx = values.map((v, i) => [v, i] as const).filter(([v]) => v !== null && Number.isFinite(v))
  idx.sort((a, b) => (a[0] as number) - (b[0] as number))
  const out: (number | null)[] = values.map(() => null)
  idx.forEach(([, i], rank) => {
    out[i] = idx.length > 1 ? rank / (idx.length - 1) : 1
  })
  return out
}

const fmt = (v: unknown, digits = 0) =>
  typeof v === 'number' ? v.toLocaleString('en-US', { maximumFractionDigits: digits }) : 'no data'

interface Props {
  grid: GridCell[]
  zones: ScoreResponse | null
  sites: Recommendation[]
  featureLabels: Record<string, string>
  selectedSiteId: string | null
  onSelectSite: (id: string) => void
  demandLabel?: string
  trip?: string | null
  /** Fly to the selected site (after the user picks one); otherwise frame every ranked site. */
  focusSelection?: boolean
}

export default function MapView({ grid, zones, sites, featureLabels, selectedSiteId, onSelectSite, demandLabel, trip, focusSelection = false }: Props) {
  const container = useRef<HTMLDivElement>(null)
  const map = useRef<maplibregl.Map | null>(null)
  const overlay = useRef<MapboxOverlay | null>(null)
  const [fade, setFade] = useState(1)

  useEffect(() => {
    if (!container.current) return
    const m = new maplibregl.Map({
      container: container.current,
      style: BASEMAP,
      center: [-95.37, 29.76],
      zoom: 9,
      attributionControl: { compact: true },
    })
    m.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right')
    const o = new MapboxOverlay({ interleaved: false, layers: [] })
    m.addControl(o)
    m.on('zoomend', () => setFade(Math.round(fadeForZoom(m.getZoom()) * 10) / 10))
    map.current = m
    overlay.current = o
    return () => {
      m.remove()
      map.current = null
      overlay.current = null
    }
  }, [])

  // Before a score exists the map shows population density; afterwards the zone score for this practice.
  const densityPct = useMemo(() => percentiles(grid.map((c) => c.pop_density_km2 as number | null)), [grid])
  const scorePct = useMemo(() => {
    if (!zones) return null
    const pct = percentiles(zones.zones.map((z) => z.score))
    return new Map(zones.zones.map((z, i) => [z.h3, pct[i] ?? 0]))
  }, [zones])
  const zoneByCell = useMemo(() => new Map((zones?.zones ?? []).map((z) => [z.h3, z])), [zones])
  const topCells = useMemo(() => zones?.top ?? [], [zones])
  // Marker radius grows with customers won (area-proportional), so the map ranks before the list is read.
  const maxWon = useMemo(() => Math.max(1, ...sites.map((r) => r.net_new_patients)), [sites])
  const radius = useCallback((r: Recommendation) => 9 + 13 * Math.sqrt(Math.max(0, r.net_new_patients) / maxWon), [maxWon])
  const selected = sites.find((r) => r.site.id === selectedSiteId)
  // Draw the selected site last so it sits on top of its neighbours.
  const drawOrder = useMemo(() => [...sites].sort((a, b) => Number(a.site.id === selectedSiteId) - Number(b.site.id === selectedSiteId)), [sites, selectedSiteId])
  const catchmentKm = trip ? kmForMinutes(TRIP_MINUTES[trip] ?? 30) : null

  useEffect(() => {
    const o = overlay.current
    if (!o) return
    const cellIndex = new Map(grid.map((c, i) => [c.h3, i]))
    const layers = [
      new H3HexagonLayer<GridCell>({
        id: 'cells',
        data: grid,
        getHexagon: (d) => d.h3,
        getFillColor: (d) => {
          const t = scorePct ? scorePct.get(d.h3) ?? 0 : densityPct[cellIndex.get(d.h3) ?? 0] ?? 0
          // Paper stays paper: the lower half fades out, only stronger cells take color.
          if (scorePct) return t < 0.45 ? [0, 0, 0, 0] : [...ramp(FIT_RAMP, (t - 0.45) / 0.55), Math.round((70 + 150 * ((t - 0.45) / 0.55)) * fade)]
          return t < 0.3 ? [0, 0, 0, 0] : [...ramp(POP_RAMP, (t - 0.3) / 0.7), Math.round((40 + 90 * ((t - 0.3) / 0.7)) * fade)]
        },
        stroked: false,
        extruded: false,
        pickable: true,
        highPrecision: false,
        updateTriggers: { getFillColor: [scorePct, densityPct, fade] },
      }),
      new H3HexagonLayer<string>({
        id: 'top-zones',
        data: topCells,
        getHexagon: (d) => d,
        filled: false,
        stroked: true,
        getLineColor: [...INK, 255],
        lineWidthMinPixels: 2,
      }),
      new ScatterplotLayer<Recommendation>({
        id: 'catchment',
        data: selected && catchmentKm ? [selected] : [],
        getPosition: (d) => [d.site.lon, d.site.lat],
        getRadius: (catchmentKm ?? 0) * 1000,
        radiusUnits: 'meters',
        filled: true,
        getFillColor: [255, 255, 255, 46],
        stroked: true,
        getLineColor: [...INK, 230],
        lineWidthMinPixels: 2,
        updateTriggers: { getRadius: catchmentKm },
      }),
      new ScatterplotLayer<Recommendation>({
        id: 'sites',
        data: drawOrder,
        getPosition: (d) => [d.site.lon, d.site.lat],
        getRadius: (d) => radius(d) + (d.site.id === selectedSiteId ? 3 : 0),
        radiusUnits: 'pixels',
        getFillColor: (d) => (d.site.id === selectedSiteId ? [...PICK, 255] : [...INK, 255]),
        getLineColor: (d) => (d.site.id === selectedSiteId ? [...INK, 255] : [255, 255, 255, 255]),
        lineWidthMinPixels: 2.5,
        stroked: true,
        pickable: true,
        onClick: (info) => info.object && onSelectSite(info.object.site.id),
        updateTriggers: { getRadius: [selectedSiteId, radius], getFillColor: selectedSiteId, getLineColor: selectedSiteId },
      }),
      new TextLayer<Recommendation>({
        id: 'site-ranks',
        data: drawOrder,
        getPosition: (d) => [d.site.lon, d.site.lat],
        getText: (d) => String(d.rank),
        getSize: (d) => (radius(d) > 14 ? 15 : 12),
        getColor: (d) => (d.site.id === selectedSiteId ? [...INK, 255] : [255, 255, 255, 255]),
        fontFamily: 'Public Sans, sans-serif',
        fontWeight: 800,
        updateTriggers: { getColor: selectedSiteId, getSize: radius },
      }),
    ]
    o.setProps({
      layers,
      getTooltip: ({ object, layer }: PickingInfo) => {
        const style = { fontFamily: 'Public Sans, sans-serif', fontSize: '14px', lineHeight: '1.4', background: '#15171c', color: '#fff', borderRadius: '8px', padding: '10px 12px', maxWidth: '300px' }
        if (object && layer?.id === 'sites') {
          const r = object as Recommendation
          return { html: `<b>${esc(r.rank)}. ${esc(r.site.address)}</b><br/>${esc(fmt(r.net_new_patients))} ${esc(demandLabel ?? 'patients')}<br/>Click to open its flyer`, style }
        }
        if (!object || layer?.id !== 'cells') return null
        const cell = object as GridCell
        const zone: Zone | undefined = zoneByCell.get(cell.h3)
        if (zone) {
          const top = Object.entries(zone.contributions ?? {})
            .sort((a, b) => b[1] - a[1])
            .slice(0, 3)
            .map(([k]) => featureLabels[k] ?? k)
          return { html: `<b>Fit ${esc(fmt((zone.score ?? 0) * 100))} / 100</b>, ${esc(cell.county)} County<br/>Demand here: ${esc(fmt(zone.demand))} ${esc(demandLabel ?? 'patients')}<br/>Strongest factors: ${top.map(esc).join(', ')}`, style }
        }
        return { html: `<b>${esc(cell.county)} County</b><br/>${esc(fmt(cell.pop_density_km2))} people per km²<br/>${esc(fmt(cell.pop_total_within_5km))} people within 5 km<br/>Median income $${esc(fmt(cell.median_hh_income))}`, style }
      },
    })
  }, [grid, densityPct, scorePct, zoneByCell, topCells, sites, selectedSiteId, featureLabels, onSelectSite, radius, selected, catchmentKm, demandLabel, drawOrder, fade])

  // Fit the three counties when the grid arrives and again whenever a run resets (no ranked sites).
  const hasSites = sites.length > 0
  useEffect(() => {
    if (!map.current || grid.length === 0 || hasSites) return
    const lats = grid.map((c) => c.cell_lat)
    const lons = grid.map((c) => c.cell_lon)
    map.current.fitBounds(
      [
        [Math.min(...lons), Math.min(...lats)],
        [Math.max(...lons), Math.max(...lats)],
      ],
      { padding: 40, duration: 600 },
    )
  }, [grid, hasSites])

  // Frame every ranked site when results land, clear of the legend (bottom-left).
  useEffect(() => {
    const m = map.current
    if (!m || sites.length === 0 || focusSelection) return
    const lons = sites.map((r) => r.site.lon)
    const lats = sites.map((r) => r.site.lat)
    m.fitBounds(
      [
        [Math.min(...lons), Math.min(...lats)],
        [Math.max(...lons), Math.max(...lats)],
      ],
      { padding: { top: 60, right: 70, bottom: 110, left: 60 }, maxZoom: 12, duration: 900 },
    )
  }, [sites, focusSelection])

  // Fly to the site the user picked, framing its catchment.
  useEffect(() => {
    const site = sites.find((s) => s.site.id === selectedSiteId)
    const m = map.current
    if (!site || !m || !focusSelection) return
    // Frame the site with its whole catchment (or a 3 km neighborhood when the trip type is unknown).
    const km = catchmentKm ?? 3
    const dLat = km / 111
    const dLon = km / (111 * Math.cos((site.site.lat * Math.PI) / 180))
    m.fitBounds(
      [
        [site.site.lon - dLon, site.site.lat - dLat],
        [site.site.lon + dLon, site.site.lat + dLat],
      ],
      { padding: { top: 36, right: 36, bottom: 150, left: 36 }, duration: 900, essential: true },
    )
  }, [selectedSiteId, sites, catchmentKm, focusSelection])

  return (
    <>
      <div ref={container} className="map" aria-label="Map of Houston scored by H3 hexagon" />
      <div className={`legend${sites.length ? ' compact' : ''}`}>
        <b>{zones ? 'Fit for this business' : 'People per km²'}</b>
        <div className={`ramp${zones ? '' : ' pop'}`} />
        <div className="ends">
          <span>Lower</span>
          <span>Higher</span>
        </div>
        {(zones || sites.length > 0) && (
          <div className="keys">
            {zones && (
              <span>
                <i className="sw-zone" /> Top {topCells.length} zones
              </span>
            )}
            {sites.length > 0 && (
              <span>
                <i className="sw-site" /> Ranked space, sized by {demandLabel ?? 'patients'} won
              </span>
            )}
            {selected && (
              <span>
                <i className="sw-pick" /> Selected{catchmentKm ? `, ${TRIP_MINUTES[trip ?? ''] ?? 30}-minute catchment` : ''}
              </span>
            )}
          </div>
        )}
      </div>
    </>
  )
}
