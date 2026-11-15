// The run in plain words for the customer view: each agent step as one sentence, with what it found.
import type { RunStatus, Scout, Todo, TraceLine } from '../state/runReducer'
import type { Factor } from '../types/contracts'
import Icon from './Icon'

const PLAIN: [RegExp, string][] = [
  [/^understand/i, 'Understanding who your customers are'],
  [/^reason/i, 'Working out what makes a location succeed for you'],
  [/^find data/i, 'Finding public data to measure it'],
  [/^approve the data/i, 'Your OK on the data'],
  [/^(weigh|approve weights)/i, 'Your OK on what matters most'],
  [/^find real spaces/i, 'Looking for spaces for lease in the best neighborhoods'],
  [/^rank/i, 'Ranking spaces by the customers you would win'],
  [/^inspect/i, 'Checking parking from the air and the 3D scans'],
  [/^write/i, 'Double-checking every number'],
]

const plain = (content: string) => PLAIN.find(([re]) => re.test(content))?.[1] ?? content

interface Props {
  todos: Todo[]
  scouts: Scout[]
  factors: Record<string, Factor>
  trace: TraceLine[]
  status: RunStatus
  profileSummary: string | null
}

export default function ProgressFeed({ todos, scouts, factors, trace, status, profileSummary }: Props) {
  const factorCount = Object.keys(factors).length
  const found = scouts.filter((s) => s.status === 'found' || s.status === 'ready')
  const checks = trace.filter((l) => l.agent === 'site-inspector' && l.kind === 'result' && l.tool !== 'plan_inspection').length

  const detail = (t: Todo): string | null => {
    if (t.status === 'pending') return null
    if (/^understand/i.test(t.content) && profileSummary) return profileSummary.split('. ')[0].replace(/\s*\(\d(\.\d+)?\)/g, '')
    if (/^reason/i.test(t.content) && factorCount) return `${factorCount} things matter for your business.`
    if (/^find data/i.test(t.content) && found.length)
      return `Found: ${found.map((s) => factors[s.factorId]?.factor ?? s.factorId).join(', ')}.`
    if (/^inspect/i.test(t.content) && checks) return `${checks} checks on the top spaces.`
    return null
  }

  return (
    <ol className="feed" aria-live="polite">
      {todos.map((t) => {
        const text = detail(t)
        const waiting = t.status === 'in_progress' && status === 'waiting' && /^(approve|weigh)/i.test(t.content)
        return (
          <li key={t.content} data-status={t.status} data-waiting={waiting}>
            <span className="mark" aria-hidden="true">
              {t.status === 'completed' ? <Icon name="check" size={14} /> : null}
            </span>
            <div>
              <p className="what">{plain(t.content)}</p>
              {text && <p className="found">{text}</p>}
              {waiting && <p className="found wait">Waiting for you below.</p>}
            </div>
          </li>
        )
      })}
    </ol>
  )
}
