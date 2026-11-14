import type { RunStatus, Scout, Todo, TraceLine } from '../state/runReducer'
import type { Factor } from '../types/contracts'

const SCOUT_LABEL: Record<Scout['status'], string> = {
  running: 'Searching',
  retrying: 'Trying another source',
  ready: 'Dataset ready',
  found: 'Found',
  proxy: 'Using a proxy',
  dropped: 'Dropped',
}
export const BRANCH_COLORS = ['var(--scout-1)', 'var(--scout-2)', 'var(--scout-3)', 'var(--scout-4)']

/** The stations a run passes, shown before a run starts (same wording as the agent's plan). */
export const PREVIEW_STOPS = [
  'Understand the business and its customers',
  'Reason about what makes a location succeed',
  'Find data for uncovered factors',
  'Approve the data plan',
  'Approve weights and score Houston',
  'Find real spaces in the top zones',
  'Rank sites by new customers won',
  'Write and verify the report',
]

// Steps where the run stops for the user: the data plan and the weights (wording differs between runs).
const isTransfer = (content: string) => /^(approve|weigh)/i.test(content)
const isScoutStop = (content: string) => /data/i.test(content) && !isTransfer(content)

interface Props {
  todos: Todo[]
  scouts: Scout[]
  factors: Record<string, Factor>
  trace: TraceLine[]
  status: RunStatus
  profileSummary: string | null
  /** ranked spaces and whether they verified, shown at the terminus once the run is done */
  result?: { count: number; verified: boolean } | null
}

export default function RunLine({ todos, scouts, factors, trace, status, profileSummary, result }: Props) {
  const stops: Todo[] = todos.length ? todos : PREVIEW_STOPS.map((content) => ({ content, status: 'pending' }))
  const scoutAt = Math.max(0, stops.findIndex((t) => isScoutStop(t.content)))
  const factorCount = Object.keys(factors).length
  const covered = Object.values(factors).filter((f) => f.status === 'covered').length

  const note = (t: Todo, i: number): string | null => {
    if (i === 0 && profileSummary) return profileSummary.split('. ')[0].replace(/\s*\(\d(\.\d+)?\)/g, '')
    if (i === 1 && factorCount) return `${factorCount} factors, ${covered} already in our data`
    if (i === scoutAt && scouts.length) return `${scouts.length} scout${scouts.length > 1 ? 's' : ''} in parallel`
    if (isTransfer(t.content) && t.status === 'in_progress' && status === 'waiting') return 'Waiting for you'
    if (i === stops.length - 1 && result) return `${result.count} spaces ranked${result.verified ? ', every number verified' : ''}`
    if (/^inspect/i.test(t.content)) {
      const checks = trace.filter((l) => l.agent === 'site-inspector' && l.kind === 'result' && l.tool !== 'plan_inspection')
      if (checks.length) return `${checks.length} checks: CoStar listing, Vexcel aerial, Matterport scan`
    }
    return null
  }

  return (
    <>
      <section className="section">
        <h2>The run</h2>
        <p className="lede">Each stop is a step the agent takes. Branches are data scouts working in parallel; double rings are where you approve.</p>
        <ol className="line" style={{ marginTop: 18 }}>
          {stops.map((t, i) => {
            const waiting = isTransfer(t.content) && t.status === 'in_progress' && status === 'waiting'
            const text = note(t, i)
            return (
              <li key={t.content} className="station" data-status={t.status} data-transfer={isTransfer(t.content)} data-waiting={waiting} data-terminus={i === stops.length - 1 && !!result}>
                <span className="stop" aria-hidden="true" />
                <div>
                  <div className="name">
                    {t.content}
                    <span className="visually-hidden"> ({t.status.replace('_', ' ')})</span>
                  </div>
                  {text && <p className={`note${waiting ? ' wait' : ''}`}>{text}</p>}
                  {i === scoutAt && scouts.length > 0 && (
                    <ul className="branches">
                      {scouts.map((s, k) => (
                        <li key={s.factorId} className="branch" data-status={s.status} style={{ ['--c' as string]: BRANCH_COLORS[k % BRANCH_COLORS.length] }}>
                          <div className="row">
                            <b>{factors[s.factorId]?.factor ?? s.factorId}</b>
                            <span className="state">
                              {SCOUT_LABEL[s.status]}
                              {s.tries > 1 && s.status !== 'found' ? `, try ${s.tries} of 3` : ''}
                            </span>
                          </div>
                          {s.last && <p>{s.last}</p>}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </li>
            )
          })}
        </ol>
      </section>

      {trace.length > 0 && (
        <section className="section">
          <details className="log">
            <summary>Every step ({trace.length})</summary>
            <ol>
              {trace.map((line) => (
                <li key={`${line.seq}-${line.kind}`} data-ok={line.ok}>
                  <span className="seq">{line.seq}</span>
                  <span>
                    <b>{line.agent.replace('dataset-scout:', 'scout, ')}</b>{' '}
                    {line.kind === 'call' ? `runs ${line.tool}${line.text ? ` (${line.text})` : ''}` : line.text || `${line.tool} done`}
                  </span>
                </li>
              ))}
            </ol>
          </details>
        </section>
      )}
    </>
  )
}
