// Plain-language verdict for a ranked space, from report values only (no new numbers).
import type { Recommendation } from '../types/contracts'

export const TIGHT_PARKING = 0.85 // same threshold as services/agent/inspector.py

export interface Verdict {
  headline: string
  notes: { text: string; tone: 'good' | 'warn' | 'plain' }[]
}

export function verdict(rec: Recommendation): Verdict {
  const headline = rec.rank === 1 ? 'Top pick' : rec.rank <= 3 ? 'Strong option' : 'Worth a look'
  const notes: Verdict['notes'] = []
  const s = rec.site
  if (s.available === true) notes.push({ text: 'For lease', tone: 'good' })
  else if (s.available === false) notes.push({ text: 'Lease pending', tone: 'warn' })
  const occ = rec.inspection?.occupancy
  if (occ != null) {
    notes.push(occ >= TIGHT_PARKING ? { text: 'Parking may be tight at peak', tone: 'warn' } : { text: 'Parking looked available from the air', tone: 'good' })
  }
  if (s.matterport_id) notes.push({ text: '3D tour available', tone: 'plain' })
  return { headline, notes }
}
