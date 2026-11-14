// Aerial imagery (Vexcel) and 3D tours (Matterport) for a site, through the dev-server proxy in vite.config.ts.
// Display only: nothing here feeds the agent or the scores.

declare const __VEXCEL__: boolean
declare const __MATTERPORT__: boolean

export const HAS_VEXCEL = typeof __VEXCEL__ !== 'undefined' && __VEXCEL__
export const HAS_MATTERPORT_API = typeof __MATTERPORT__ !== 'undefined' && __MATTERPORT__

/** URL of a north-up aerial image centred on the point, `meters` wide (null when no Vexcel key is configured). */
export function aerialUrl(lat: number, lon: number, meters = 160, px = 640): string | null {
  if (!HAS_VEXCEL) return null
  const q = new URLSearchParams({
    layer: 'urban', wkt: `POINT(${lon.toFixed(6)} ${lat.toFixed(6)})`, srid: '4326',
    'bbox-dimensions': `${meters},${meters}`, 'destination-dimensions': `${px},${px}`, 'image-format': 'jpeg',
  })
  return `/vendor/vexcel/ortho/extract?${q}`
}

export interface CarCount {
  cars: number
  boxes: number[][][] // lon/lat rings
  meters: number
}

/** Parked cars Vexcel detects on the latest aerial in a square around the point. */
export async function countParkedCars(lat: number, lon: number, meters = 120): Promise<CarCount> {
  const resp = await fetch('/vendor/vexcel/elements/detect', {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      wkt: `POINT(${lon} ${lat})`, 'bbox-dimensions': [meters, meters],
      images: [{ endpoint: 'ortho/extract', request: { layer: 'urban' } }],
      'model-input': { elements: [{ label: 'parked car', 'detection-threshold': 0.4 }] },
    }),
  })
  if (!resp.ok) throw new Error(`Vexcel detection failed (HTTP ${resp.status})`)
  const data = (await resp.json()) as { features?: { geometry: { coordinates: number[][][] }; properties?: { label?: string } }[] }
  const feats = (data.features ?? []).filter((f) => f.properties?.label === 'parked car')
  return { cars: feats.length, boxes: feats.map((f) => f.geometry.coordinates[0]), meters }
}

export interface ScanInfo {
  id: string
  name?: string
  areaSqft?: number
  rooms?: number
  floors?: number
  embed: string
}

/** Matterport public player URL (no key needed); details from the Model API when keys are configured. */
export async function matterportScan(id: string): Promise<ScanInfo> {
  const embed = `https://my.matterport.com/show/?m=${encodeURIComponent(id)}&play=1&qs=1`
  if (!HAS_MATTERPORT_API) return { id, embed }
  try {
    const query = `{ model(id:"${id.replace(/[^A-Za-z0-9]/g, '')}"){ name dimensions{ areaFloor } floors{ label } rooms{ label } } }`
    const resp = await fetch('/vendor/matterport', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ query }) })
    const m = ((await resp.json()) as { data?: { model?: { name?: string; dimensions?: { areaFloor?: number }; floors?: unknown[]; rooms?: unknown[] } } }).data?.model
    if (!m) return { id, embed }
    return { id, embed, name: m.name, areaSqft: m.dimensions?.areaFloor ? Math.round(m.dimensions.areaFloor) : undefined, rooms: m.rooms?.length, floors: m.floors?.length }
  } catch {
    return { id, embed }
  }
}
