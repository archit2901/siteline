import { MapboxOverlay } from '@deck.gl/mapbox'
import { H3HexagonLayer } from '@deck.gl/geo-layers'
import { ScatterplotLayer, TextLayer } from '@deck.gl/layers'
import type { PickingInfo } from '@deck.gl/core'
import * as maplibregl from 'maplibre-gl'
import { useEffect, useMemo, useRef } from 'react'
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
const RAMP: RGB[] = [
  [0xee, 0xf2, 0xdf],
  [0xa9, 0xcf, 0xa4],
  [0x4e, 0x9a, 0x84],
  [0x1f, 0x58, 0x66],
]
const AMBER: RGB = [0xe3, 0x9b, 0x2d]
const INK: RGB = [0x1c, 0x27, 0x33]

function ramp(t: number): RGB {
  const x = Math.min(1, Math.max(0, t)) * (RAMP.length - 1)
  const i = Math.min(RAMP.length - 2, Math.floor(x))
  const f = x - i
  return [0, 1, 2].map((k) => Math.round(RAMP[i][k] + (RAMP[i + 1][k] - RAMP[i][k]) * f)) as RGB
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
}

export default function MapView({ grid, zones, sites, featureLabels, selectedSiteId, onSelectSite }: Props) {
  const container = useRef<HTMLDivElement>(null)
  const map = useRef<maplibregl.Map | null>(null)
  const overlay = useRef<MapboxOverlay | null>(null)

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
          return [...ramp(t), 170]
        },
        stroked: false,
        extruded: false,
        pickable: true,
        highPrecision: false,
        updateTriggers: { getFillColor: [scorePct, densityPct] },
      }),
      new H3HexagonLayer<string>({
        id: 'top-zones',
        data: topCells,
        getHexagon: (d) => d,
        filled: false,
        stroked: true,
        getLineColor: [...AMBER, 255],
        lineWidthMinPixels: 2.5,
      }),
      new ScatterplotLayer<Recommendation>({
        id: 'sites',
        data: sites,
        getPosition: (d) => [d.site.lon, d.site.lat],
        getRadius: (d) => (d.site.id === selectedSiteId ? 15 : 11),
        radiusUnits: 'pixels',
        getFillColor: (d) => (d.site.id === selectedSiteId ? [...AMBER, 255] : [...INK, 255]),
        getLineColor: [255, 255, 255, 255],
        lineWidthMinPixels: 2,
        stroked: true,
        pickable: true,
        onClick: (info) => info.object && onSelectSite(info.object.site.id),
        updateTriggers: { getRadius: selectedSiteId, getFillColor: selectedSiteId },
      }),
      new TextLayer<Recommendation>({
        id: 'site-ranks',
        data: sites,
        getPosition: (d) => [d.site.lon, d.site.lat],
        getText: (d) => String(d.rank),
        getSize: 13,
        getColor: [255, 255, 255, 255],
        fontFamily: 'Atkinson Hyperlegible, sans-serif',
        fontWeight: 700,
      }),
    ]
    o.setProps({
      layers,
      getTooltip: ({ object, layer }: PickingInfo) => {
        if (!object || layer?.id !== 'cells') return null
        const cell = object as GridCell
        const zone: Zone | undefined = zoneByCell.get(cell.h3)
        const style = { fontFamily: 'Atkinson Hyperlegible, sans-serif', fontSize: '13px', background: '#1c2733', color: '#fff', borderRadius: '6px', padding: '8px 10px', maxWidth: '280px' }
        if (zone) {
          const top = Object.entries(zone.contributions ?? {})
            .sort((a, b) => b[1] - a[1])
            .slice(0, 3)
            .map(([k]) => featureLabels[k] ?? k)
          return { html: `<b>Fit ${fmt(zone.score, 2)}</b> in ${cell.county} County<br/>Expected demand ${fmt(zone.demand)} a year<br/>Strongest: ${top.join(', ')}`, style }
        }
        return { html: `<b>${cell.county} County</b><br/>${fmt(cell.pop_density_km2)} people per km²<br/>${fmt(cell.pop_total_within_5km)} people within 5 km<br/>Median income $${fmt(cell.median_hh_income)}`, style }
      },
    })
  }, [grid, densityPct, scorePct, zoneByCell, topCells, sites, selectedSiteId, featureLabels, onSelectSite])

  // Fit the three counties once the grid arrives.
  useEffect(() => {
    if (!map.current || grid.length === 0) return
    const lats = grid.map((c) => c.cell_lat)
    const lons = grid.map((c) => c.cell_lon)
    map.current.fitBounds(
      [
        [Math.min(...lons), Math.min(...lats)],
        [Math.max(...lons), Math.max(...lats)],
      ],
      { padding: 40, duration: 0 },
    )
  }, [grid])

  // Fly to the selected site.
  useEffect(() => {
    const site = sites.find((s) => s.site.id === selectedSiteId)
    const m = map.current
    if (!site || !m) return
    // Keep the pin above the results drawer, which covers about half the map.
    const bottom = Math.round(m.getContainer().clientHeight * 0.52)
    m.flyTo({ center: [site.site.lon, site.site.lat], zoom: 11.5, duration: 900, padding: { top: 40, bottom, left: 0, right: 0 } })
  }, [selectedSiteId, sites])

  return (
    <>
      <div ref={container} className="map" aria-label="Map of Houston scored by H3 hexagon" />
      <div className="legend">
        <strong>{zones ? 'Fit for this practice' : 'People per km²'}</strong>
        <div className="ramp" />
        <div className="ends">
          <span>Lower</span>
          <span>Higher</span>
        </div>
        {zones && (
          <div className="top">
            <span className="swatch" /> Top {topCells.length} zones
          </div>
        )}
      </div>
    </>
  )
}
