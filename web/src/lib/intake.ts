// Intake answers <-> PracticeProfile. Mirrors services/agent/intake.py so the form and the chat store answers the
// same way: contract fields where one exists, everything else as "[id] Question: answer" lines in free_text.
import type { IntakeQuestion } from '../api/client'
import type { PracticeProfile } from '../types/contracts'

export const FIRST_LOCATION = 'none yet (this will be the first)'
const LINE = /^\[([a-z_]+)\] [^:]{1,80}: (.+)$/

export const PAYER_IDS: Record<string, string> = {
  Medicare: 'medicare', Medicaid: 'medicaid', CHIP: 'chip', Commercial: 'commercial', "Workers' comp": 'workers_comp',
  'Self-pay': 'self_pay', 'Uninsured / sliding fee': 'self_pay',
}
export const PAYER_LABELS = Object.fromEntries(Object.entries(PAYER_IDS).map(([k, v]) => [v, k]))

export const label = (q: IntakeQuestion) => q.text.split('?')[0].replace(/:/g, '').slice(0, 80)

/** free_text -> answers by question id, plus any other notes. */
export function parseAnswers(freeText: string | undefined): { answers: Record<string, string>; notes: string } {
  const answers: Record<string, string> = {}
  const other: string[] = []
  for (const raw of (freeText ?? '').split('\n')) {
    const line = raw.trim()
    const m = LINE.exec(line)
    if (m) answers[m[1]] = m[2]
    else if (line) other.push(line)
  }
  return { answers, notes: other.join(' ') }
}

export function composeFreeText(answers: Record<string, string>, notes: string, questions: IntakeQuestion[]): string {
  const byId = Object.fromEntries(questions.map((q) => [q.id, q]))
  const lines = Object.entries(answers)
    .filter(([, v]) => v.trim())
    .map(([k, v]) => `[${k}] ${byId[k] ? label(byId[k]) : k}: ${v.trim().replace(/\s+/g, ' ')}`)
  return [...lines, ...(notes.trim() ? [notes.trim()] : [])].join('\n').slice(0, 3000)
}

/** What a run still needs (same rule as intake.missing_for). */
export function missingFor(p: PracticeProfile, answers: Record<string, string>, sectorQs: IntakeQuestion[]): string[] {
  if (!(p.business_type || p.specialty)) return ['what the business is']
  const out: string[] = []
  if (!p.customers?.trim()) out.push('who your customers are')
  const placed = (p.existing_offices ?? []).some((o) => o.address?.trim() || o.lat != null)
  if (!placed && !answers.locations) out.push('your current locations (or tick “first location”)')
  for (const q of sectorQs) {
    if (!q.required) continue
    const filled = q.field === 'services' ? (p.services ?? []).some((s) => s.trim())
      : q.field === 'payers' ? (p.payers_accepted ?? []).length > 0 : Boolean(answers[q.id]?.trim())
    if (!filled) out.push(label(q).toLowerCase())
  }
  return out
}
