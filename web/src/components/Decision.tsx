import type { Review } from '../api/client'
import { RuleBox } from '../lib/expert'

const MARK: Record<string, string> = { met: '✓', failed: '✕', unknown: '?', supports: '✓', risk: '!', open: '?', not_applicable: '–' }
const STATUS: Record<string, string> = { confirmed: 'Confirmed', provisional: 'Provisional', ineligible: 'Not eligible' }

/** Decision first: the pick or provisional pick, why, the strongest alternative, what would change it, then each
 *  reviewed space's hard requirements and the six perspectives (Python, from stored numbers; review.json). */
export default function Decision({ r, onOpen }: { r: Review; onOpen: (siteId: string) => void }) {
  return (
    <div className="space decision">
      <div className="callout">
        <div className="kicker"><span>Decision</span></div>
        <p><b>{r.decision}</b></p>
        {r.why && <p>{r.why}</p>}
        <p className="sub">Objective: {r.objective} Compared with: {r.baseline}</p>
      </div>
      <RuleBox title="Expert rules behind this pick" />
      {(r.alternative || r.what_could_change.length > 0) && (
        <div className="callout">
          <div className="kicker"><span>What could change it</span></div>
          {r.alternative && <p>{r.alternative}</p>}
          <ul className="changes">{r.what_could_change.map((c) => <li key={c}>{c}</li>)}</ul>
        </div>
      )}
      {r.sites.map((s) => (
        <div key={s.site_id} className={`callout review ${s.status}`}>
          <div className="kicker">
            <span>#{s.rank} · {STATUS[s.status]}</span>
            <button type="button" className="linkbtn" onClick={() => onOpen(s.site_id)}>{s.address}</button>
          </div>
          <ul className="reqs">{s.requirements.map((q) => (
            <li key={q.name} data-status={q.status}><span className="mark">{MARK[q.status]}</span>
              <span><b>{q.name}</b> {q.detail}{q.status !== 'met' && q.verify && <small>Verify: {q.verify}</small>}</span></li>))}
          </ul>
          <dl className="persp">{s.notes.map((n) => (
            <div key={n.perspective} data-status={n.status}>
              <dt><span className="mark">{MARK[n.status]}</span>{n.perspective}</dt>
              <dd><small>{n.question}</small>{n.finding}</dd>
            </div>))}
          </dl>
        </div>
      ))}
    </div>
  )
}
