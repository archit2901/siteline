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

/** One intake question (services/agent/questions.py); not a contract type, the agent-api serves it as-is. */
export interface IntakeQuestion {
  id: string
  text: string
  why: string
  kind: 'text' | 'long' | 'choice' | 'multi' | 'locations' | 'weights'
  options: string[]
  placeholder: string
  required: boolean
  field: string
}
export interface QuestionSet {
  sector: string
  label: string
  base: IntakeQuestion[]
  sector_questions: IntakeQuestion[]
}

/** Base questions + the sector set for what the owner typed (no model call). */
export function intakeQuestions(business: string, specialty = ''): Promise<QuestionSet> {
  const q = new URLSearchParams({ business, specialty })
  return request(`/intake/questions?${q}`)
}

/** Intake step one: read the owner's website and pre-fill what it states. */
export function intakeWebsite(url: string, draft: PracticeProfile | null): Promise<IntakeResponse> {
  return request('/intake/website', { method: 'POST', body: JSON.stringify({ url, draft }) })
}

/** strategy.json (services/agent/strategy.py): not a contract type. */
export interface TenureOption { option: 'lease' | 'buy' | 'build'; annual_cost_usd: number; upfront_usd: number; notes: string }
export interface Strategy {
  program: { service_lines: string[]; base_sqft: [number, number]; recommended_sqft: [number, number]; requirements: string[]; special_buildout_usd: number }
  visibility: 'high' | 'medium' | 'low'
  visibility_reason: string
  preferred_space_uses: string[]
  tenure_preference: string
  recommendation: string
  sites: { site_id: string; address: string; sqft?: number | null; options: TenureOption[]; cheapest: string }[]
  assumptions: Record<string, number>
}
/** review.json (services/agent/review.py): hard requirements and the six perspectives. Not a contract type. */
export interface ReviewRequirement { name: string; status: 'met' | 'failed' | 'unknown'; detail: string; verify: string }
export interface ReviewNote { perspective: string; question: string; finding: string; status: 'supports' | 'risk' | 'open' | 'not_applicable'; evidence: string[] }
export interface SiteReview { site_id: string; address: string; rank: number; status: 'confirmed' | 'provisional' | 'ineligible'; requirements: ReviewRequirement[]; notes: ReviewNote[] }
export interface Review { objective: string; baseline: string; decision: string; why: string; alternative: string; what_could_change: string[]; sites: SiteReview[] }
export function loadReview(runId: string): Promise<Review> {
  return request(`/runs/${runId}/artifacts/review.json`)
}
export function loadStrategy(runId: string): Promise<Strategy> {
  return request(`/runs/${runId}/artifacts/strategy.json`)
}

/** expert.json (services/agent/expert.py): expert-rule vs AI provenance per factor. Not a contract type. */
export function loadExpert(runId: string): Promise<import('../lib/expert').ExpertReport> {
  return request(`/runs/${runId}/artifacts/expert.json`)
}

/** options.json (services/agent/expert.py program_options): smaller programs and their best lease spaces. */
export interface OptionSite { site_id: string; address: string; sqft: number | null; rent_psf: number | null; annual_rent: number | null; net_new: number; final_score: number }
export interface ProgramOption { id: string; label: string; functions: Record<string, number>; left_out: Record<string, string>; min_sqft: number; max_sqft: number; parking_per_1000sf: number; sites: OptionSite[] }
export function loadOptions(runId: string): Promise<ProgramOption[]> {
  return request(`/runs/${runId}/artifacts/options.json`)
}
