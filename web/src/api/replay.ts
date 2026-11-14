// Fixture mode: plays public/fixtures/events.jsonl through the same reducer as the live SSE stream.
import type { Report, RunEvent, ScoreResponse } from '../types/contracts'
import type { RunAction } from '../state/runReducer'

export const FIXTURE_BASE = '/fixtures'

export async function loadFixtureEvents(base = FIXTURE_BASE): Promise<RunEvent[]> {
  const resp = await fetch(`${base}/events.jsonl`)
  if (!resp.ok) throw new Error(`Could not load ${base}/events.jsonl (HTTP ${resp.status}). Run "npm run fixtures".`)
  const text = await resp.text()
  return text.split('\n').filter(Boolean).map((line) => JSON.parse(line) as RunEvent)
}

export async function loadArtifact(name: string, base = FIXTURE_BASE): Promise<RunAction | null> {
  if (name !== 'zones.json' && name !== 'report.json') return null
  const resp = await fetch(`${base}/${name}`)
  if (!resp.ok) throw new Error(`Could not load ${base}/${name} (HTTP ${resp.status}).`)
  const data: unknown = await resp.json()
  return name === 'zones.json'
    ? { type: 'artifact', name, data: data as ScoreResponse }
    : { type: 'artifact', name, data: data as Report }
}

export interface Replay {
  /** Resolves when the run has played to the end or was stopped. */
  finished: Promise<void>
  /** Continue after an approval event. */
  answer: (approvalId: string) => void
  stop: () => void
}

/** Plays events ~intervalMs apart; pauses on each approval until answer() is called with its id. */
export function startReplay(
  events: RunEvent[],
  dispatch: (action: RunAction) => void,
  { intervalMs = 400, runId = 'replay', base = FIXTURE_BASE }: { intervalMs?: number; runId?: string; base?: string } = {},
): Replay {
  let stopped = false
  let release: ((id: string) => void) | null = null
  let wake: (() => void) | null = null

  const sleep = (ms: number) =>
    new Promise<void>((resolve) => {
      const timer = setTimeout(resolve, ms)
      wake = () => {
        clearTimeout(timer)
        resolve()
      }
    })

  const finished = (async () => {
    dispatch({ type: 'start', runId })
    for (const raw of events) {
      if (stopped) return
      const event: RunEvent = { ...raw, run_id: runId }
      dispatch({ type: 'event', event })
      if (event.type === 'file') {
        const action = await loadArtifact(String(event.payload?.name ?? ''), base)
        if (action && !stopped) dispatch(action)
      }
      if (event.type === 'approval') {
        const approvalId = String(event.payload?.approval_id ?? '')
        await new Promise<void>((resolve) => {
          release = (id) => {
            if (id === approvalId) resolve()
          }
          wake = resolve
        })
        release = null
        if (!stopped) dispatch({ type: 'approvalAnswered', approvalId })
      }
      await sleep(intervalMs)
    }
  })()

  return {
    finished,
    answer: (id) => release?.(id),
    stop: () => {
      stopped = true
      wake?.()
    },
  }
}
