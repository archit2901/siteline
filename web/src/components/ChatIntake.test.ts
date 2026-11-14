import { describe, expect, it } from 'vitest'
import { PRESETS } from './IntakeForm'
import { presetSentence } from './ChatIntake'

describe('presetSentence', () => {
  it('turns every example into a sentence an owner would type', () => {
    for (const p of PRESETS) {
      const s = presetSentence(p)
      expect(/^An? /.test(s)).toBe(true)
      expect(s.length).toBeGreaterThan(8)
      expect(s).not.toMatch(/^A [aeiou]/i)
    }
  })
})

import { singular } from './SiteCards'

describe('singular', () => {
  it('singularizes the plural noun, not the qualifier', () => {
    expect(singular('children under 18')).toBe('child under 18')
    expect(singular('adults 18-44')).toBe('adult 18-44')
    expect(singular('renter households')).toBe('renter household')
    expect(singular('patients')).toBe('patient')
  })
})
