// RunEvent stream -> UI state. Payload conventions: services/agent/CLAUDE.md ("Events").
import type { ApprovalRequest, Factor, Report, RunEvent, ScoreResponse } from '../types/contracts'

export type TodoStatus = 'pending' | 'in_progress' | 'completed'
export interface Todo {
  content: string
  status: TodoStatus
}

export type ScoutStatus = 'running' | 'retrying' | 'ready' | 'found' | 'proxy' | 'dropped'
export interface Scout {
  factorId: string
  tries: number
  status: ScoutStatus
  last: string
}

export interface TraceLine {
  seq: number
  agent: string
  kind: 'call' | 'result'
  tool: string
  text: string
  ok: boolean
  factorId?: string
}

export type RunStatus = 'idle' | 'running' | 'waiting' | 'done' | 'error'

export interface RunState {
  runId: string | null
  status: RunStatus
  todos: Todo[]
  factorOrder: string[]
  factors: Record<string, Factor>
  scoutOrder: string[]
  scouts: Record<string, Scout>
  trace: TraceLine[]
  approval: ApprovalRequest | null
  approvalsSeen: string[]
  files: string[]
  zones: ScoreResponse | null
  report: Report | null
  error: string | null
}

export type RunAction =
  | { type: 'start'; runId: string }
  | { type: 'event'; event: RunEvent }
  | { type: 'artifact'; name: 'zones.json'; data: ScoreResponse }
  | { type: 'artifact'; name: 'report.json'; data: Report }
  | { type: 'approvalAnswered'; approvalId: string }
  | { type: 'reset' }

export const initialRunState: RunState = {
  runId: null,
  status: 'idle',
  todos: [],
  factorOrder: [],
  factors: {},
  scoutOrder: [],
  scouts: {},
  trace: [],
  approval: null,
  approvalsSeen: [],
  files: [],
  zones: null,
  report: null,
  error: null,
}

const SCOUT_DONE: ScoutStatus[] = ['found', 'proxy', 'dropped']

function str(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

function upsertScout(state: RunState, factorId: string, change: (s: Scout) => Scout): RunState {
  const prev = state.scouts[factorId] ?? { factorId, tries: 0, status: 'running' as ScoutStatus, last: '' }
  return {
    ...state,
    scoutOrder: state.scouts[factorId] ? state.scoutOrder : [...state.scoutOrder, factorId],
    scouts: { ...state.scouts, [factorId]: change(prev) },
  }
}

function applyEvent(state: RunState, event: RunEvent): RunState {
  const payload = (event.payload ?? {}) as Record<string, unknown>
  const factorId = typeof payload.factor_id === 'string' ? payload.factor_id : undefined

  switch (event.type) {
    case 'todo':
      return { ...state, todos: (payload.todos as Todo[] | undefined) ?? [] }

    case 'factor': {
      const factor = payload as unknown as Factor
      const next: RunState = {
        ...state,
        factorOrder: state.factors[factor.id] ? state.factorOrder : [...state.factorOrder, factor.id],
        factors: { ...state.factors, [factor.id]: factor },
      }
      const status = factor.status as ScoutStatus | undefined
      if (status && SCOUT_DONE.includes(status) && state.scouts[factor.id]) {
        return upsertScout(next, factor.id, (s) => ({ ...s, status }))
      }
      return next
    }

    case 'tool_call':
    case 'tool_result': {
      const isCall = event.type === 'tool_call'
      const tool = str(payload.tool, 'tool')
      const ok = isCall ? true : payload.ok !== false
      const text = isCall ? describeArgs(payload.args) : str(payload.summary)
      const line: TraceLine = { seq: event.seq, agent: event.agent, kind: isCall ? 'call' : 'result', tool, text, ok, factorId }
      let next: RunState = { ...state, trace: [...state.trace, line] }
      if (factorId && event.agent.startsWith('dataset-scout')) {
        next = upsertScout(next, factorId, (s) => {
          if (SCOUT_DONE.includes(s.status)) return s
          if (isCall) return { ...s, tries: tool === 'preview_dataset' ? s.tries + 1 : s.tries, status: s.status === 'retrying' ? 'retrying' : 'running' }
          if (!ok) return { ...s, status: 'retrying', last: text }
          return { ...s, status: tool === 'preview_dataset' ? 'ready' : s.status, last: text }
        })
      }
      return next
    }

    case 'approval': {
      const approval = payload as unknown as ApprovalRequest
      return { ...state, status: 'waiting', approval, approvalsSeen: [...state.approvalsSeen, approval.approval_id] }
    }

    case 'file':
      return { ...state, files: [...state.files, str(payload.name)] }

    case 'done':
      return { ...state, status: 'done', approval: null }

    case 'error':
      return { ...state, status: payload.recoverable ? state.status : 'error', error: str(payload.message, 'The run stopped.') }

    default:
      return state
  }
}

function describeArgs(args: unknown): string {
  if (!args || typeof args !== 'object') return ''
  return Object.entries(args as Record<string, unknown>)
    .map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(', ') : String(v)}`)
    .join(', ')
}

export function runReducer(state: RunState, action: RunAction): RunState {
  switch (action.type) {
    case 'start':
      return { ...initialRunState, runId: action.runId, status: 'running' }
    case 'event':
      return applyEvent(state, action.event)
    case 'artifact':
      return action.name === 'zones.json' ? { ...state, zones: action.data } : { ...state, report: action.data }
    case 'approvalAnswered':
      if (state.approval?.approval_id !== action.approvalId) return state
      return { ...state, approval: null, status: state.status === 'waiting' ? 'running' : state.status }
    case 'reset':
      return initialRunState
  }
}
