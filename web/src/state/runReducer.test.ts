import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import type { Report, RunEvent, ScoreResponse } from '../types/contracts'
import { initialRunState, runReducer, type RunState } from './runReducer'

const FIXTURES = join(__dirname, '..', '..', '..', 'fixtures')
const events: RunEvent[] = readFileSync(join(FIXTURES, 'events.jsonl'), 'utf8')
  .split('\n')
  .filter(Boolean)
  .map((line) => JSON.parse(line) as RunEvent)

function fold(): RunState {
  let state = runReducer(initialRunState, { type: 'start', runId: 'test' })
  for (const event of events) {
    state = runReducer(state, { type: 'event', event })
    if (event.type === 'approval') {
      state = runReducer(state, { type: 'approvalAnswered', approvalId: String(event.payload?.approval_id) })
    }
    if (event.type === 'file' && event.payload?.name === 'zones.json') {
      const zones = JSON.parse(readFileSync(join(FIXTURES, 'zones.json'), 'utf8')) as ScoreResponse
      state = runReducer(state, { type: 'artifact', name: 'zones.json', data: zones })
    }
    if (event.type === 'file' && event.payload?.name === 'report.json') {
      const report = JSON.parse(readFileSync(join(FIXTURES, 'report.json'), 'utf8')) as Report
      state = runReducer(state, { type: 'artifact', name: 'report.json', data: report })
    }
  }
  return state
}

describe('runReducer over the fixture run', () => {
  const state = fold()

  it('finishes with every todo completed', () => {
    expect(state.status).toBe('done')
    expect(state.todos.length).toBeGreaterThan(0)
    expect(state.todos.every((t) => t.status === 'completed')).toBe(true)
  })

  it('keeps one entry per factor with its latest status', () => {
    expect(state.factorOrder).toHaveLength(9)
    const statuses = state.factorOrder.map((id) => state.factors[id].status)
    expect(statuses).not.toContain('searching')
    expect(statuses).toContain('proxy')
  })

  it('tracks each scout, including the retry', () => {
    expect(state.scoutOrder).toHaveLength(3)
    expect(state.scouts.schools_nearby.tries).toBe(2)
    expect(state.scoutOrder.every((id) => ['found', 'proxy', 'dropped'].includes(state.scouts[id].status))).toBe(true)
  })

  it('sees both approvals and loads both artifacts', () => {
    expect(state.approvalsSeen).toEqual(['appr_data_plan', 'appr_weights'])
    expect(state.approval).toBeNull()
    expect(state.zones?.zones.length).toBe(11720)
    expect(state.report?.recommendations).toHaveLength(10)
  })

  it('waits while an approval is open', () => {
    let s = runReducer(initialRunState, { type: 'start', runId: 'x' })
    const approval = events.find((e) => e.type === 'approval')!
    s = runReducer(s, { type: 'event', event: approval })
    expect(s.status).toBe('waiting')
    s = runReducer(s, { type: 'approvalAnswered', approvalId: 'someone-else' })
    expect(s.status).toBe('waiting')
  })
})
