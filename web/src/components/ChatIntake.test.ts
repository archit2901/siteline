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
