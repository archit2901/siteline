// agent-api client (live mode). Base URL from VITE_AGENT_URL.
import type {
  ApproveRequest,
  AskResponse,
  ChatMessage,
  IntakeResponse,
  PracticeProfile,
  Report,
  RescoreResponse,
  RunCreate,
  RunCreated,
  ScoringSpec,
} from '../types/contracts'
import type { RunAction } from '../state/runReducer'

export const AGENT_URL = (import.meta.env.VITE_AGENT_URL ?? 'http://localhost:8000').replace(/\/$/, '')

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${AGENT_URL}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  })
  if (!resp.ok) {
    const detail = await resp.text().catch(() => '')
    throw new Error(`${init?.method ?? 'GET'} ${path} failed (HTTP ${resp.status}). ${detail.slice(0, 200)}`)
  }
  return (await resp.json()) as T
}

/** A live run, or with `golden` a replay of that recorded run (paused at approvals like a live one). */
export function createRun(profile: PracticeProfile, golden?: string): Promise<RunCreated> {
  const body: RunCreate = { profile, region_id: profile.region_id ?? 'hou', mode: golden ? 'replay' : 'live', golden }
  return request('/runs', { method: 'POST', body: JSON.stringify(body) })
}

/** Names of the recorded runs agent-api can replay (empty if it has none or is unreachable). */
export async function listGolden(): Promise<string[]> {
  try {
    return (await request<{ golden: string[] }>('/golden')).golden
  } catch {
    return []
  }
}

export function approve(runId: string, body: ApproveRequest): Promise<unknown> {
  return request(`/runs/${runId}/approve`, { method: 'POST', body: JSON.stringify(body) })
}

export function rescore(runId: string, spec: ScoringSpec): Promise<RescoreResponse> {
  return request(`/runs/${runId}/rescore`, { method: 'POST', body: JSON.stringify({ spec }) })
}

export async function loadRunArtifact(runId: string, name: string): Promise<RunAction | null> {
  if (name !== 'zones.json' && name !== 'report.json') return null
  const data = await request<unknown>(`/runs/${runId}/artifacts/${name}`)
  return name === 'zones.json'
    ? { type: 'artifact', name, data: data as never }
    : { type: 'artifact', name, data: data as never }
}

/** One chat turn: the conversation so far and the draft profile -> the next reply and the updated profile. */
export function intakeChat(messages: ChatMessage[], draft: PracticeProfile | null): Promise<IntakeResponse> {
  return request('/intake', { method: 'POST', body: JSON.stringify({ messages, draft }) })
}

/** A question about a finished run, answered from facts rendered from its verified report. */
export function ask(question: string, report: Report): Promise<AskResponse> {
  return request('/ask', { method: 'POST', body: JSON.stringify({ question, report }) })
}
