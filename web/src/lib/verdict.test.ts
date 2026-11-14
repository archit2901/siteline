import { describe, expect, it } from 'vitest'
import type { Recommendation } from '../types/contracts'
import { verdict } from './verdict'

const rec = (over: Partial<Recommendation> & { occupancy?: number | null; available?: boolean | null; tour?: boolean }): Recommendation =>
  ({
    rank: over.rank ?? 1,
    net_new_patients: 1000,
    zone_score: 0.8,
    site_quality: 0.9,
    final_score: 0.9,
    justification: [],
    site: { id: 'a', address: '1 Main St', lat: 29.7, lon: -95.4, available: over.available ?? true, matterport_id: over.tour ? 'abc' : null },
    inspection: over.occupancy === undefined ? null : { occupancy: over.occupancy },
  }) as Recommendation

describe('verdict', () => {
  it('ranks headlines and flags tight parking from the aerial check', () => {
    const v = verdict(rec({ rank: 1, occupancy: 1.3, tour: true }))
    expect(v.headline).toBe('Top pick')
    expect(v.notes.map((n) => n.text)).toEqual(['For lease', 'Parking may be tight at peak', '3D tour available'])
  })
  it('says nothing about parking when it was not checked, and reports a pending lease', () => {
    const v = verdict(rec({ rank: 3, available: false }))
    expect(v.headline).toBe('Strong option')
    expect(v.notes).toEqual([{ text: 'Lease pending', tone: 'warn' }])
  })
  it('reports available parking below the threshold', () => {
    expect(verdict(rec({ rank: 5, occupancy: 0.4 })).notes.map((n) => n.text)).toContain('Parking looked available from the air')
  })
})
