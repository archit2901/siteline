import { useEffect, useRef } from 'react'
import { safeHref } from '../lib/safe'
import type { ApprovalRequest } from '../types/contracts'
import Icon from './Icon'

interface Props {
  approval: ApprovalRequest
  featureLabels: Record<string, string>
  /** Offline demo: the replay continues either way, so only Approve is offered. */
  readOnly: boolean
  recorded: boolean
  onApprove: () => void
  onReject: () => void
}

const CHECK: Record<string, string> = {
  invalid: 'Not usable, replaced',
  found_not_ingested: 'Found, not ingestible',
  valid: 'Preview checked',
  approved: 'Approved',
  proposed: 'Ready to ingest',
}

/** Docked over the map (not a modal): the map and the run stay visible while the run waits at this transfer. */
export default function ApprovalPanel({ approval, featureLabels, readOnly, recorded, onApprove, onReject }: Props) {
  const approveRef = useRef<HTMLButtonElement>(null)
  useEffect(() => approveRef.current?.focus(), [approval.approval_id])

  const isPlan = approval.kind === 'data_plan'
  const candidates = approval.candidates ?? []
  const factors = approval.factors ?? []
  const weights = Object.entries(approval.spec?.weights ?? {}).sort((a, b) => b[1] - a[1])
  const maxWeight = Math.max(...weights.map(([, w]) => w), 0.0001)

  return (
    <aside className="approval" role="region" aria-labelledby="approval-title">
      <header>
        <span className="stop" aria-hidden="true" />
        <div>
          <h2 id="approval-title">{isPlan ? 'Approve the data plan' : 'Approve the weights'}</h2>
          <p className="lede">
            {isPlan
              ? 'Public datasets the scouts found for factors our stock data does not cover. Nothing is downloaded until you approve.'
              : 'How much each factor counts toward a zone’s fit, from the priorities the agent set. Every color on the map comes from these weights.'}
          </p>
        </div>
      </header>

      <div className="body">
        {isPlan ? (
          candidates.length ? (
            <ul>
              {candidates.map((c) => {
                const href = safeHref(c.url)
                return (
                  <li className="dataset" key={c.id}>
                    <div className="for">For: {factors.find((f) => f.id === c.factor_id)?.factor ?? c.factor_id}</div>
                    <h3>
                      {href ? (
                        <a href={href} target="_blank" rel="noreferrer">
                          {c.title}
                        </a>
                      ) : (
                        c.title
                      )}
                    </h3>
                    {c.why && <p>{c.why}</p>}
                    <div className="meta">
                      <span className="chip">{c.publisher}</span>
                      <span className="chip">{c.geo_level === 'point' ? 'Point locations' : `By ${c.geo_level}`}</span>
                      <span className="chip" data-status={c.status === 'invalid' ? 'dropped' : 'found'}>
                        {CHECK[c.status ?? 'proposed']}
                      </span>
                    </div>
                  </li>
                )
              })}
            </ul>
          ) : (
            <p className="empty">The scouts found no new datasets; the run continues with stock data only.</p>
          )
        ) : (
          <div className="weights">
            {weights.map(([feature, w]) => (
              <div className="weight" key={feature}>
                <span>{featureLabels[feature] ?? feature}</span>
                <span className="wbar" aria-hidden="true">
                  <span style={{ width: `${(w / maxWeight) * 100}%` }} />
                </span>
                <span className="pct">{Math.round(w * 100)}%</span>
              </div>
            ))}
          </div>
        )}
      </div>

      <footer>
        {(readOnly || recorded) && <span className="hint">{recorded ? 'Recorded run: the saved run continues as it was approved.' : 'Offline demo: approving continues the replay.'}</span>}
        {!readOnly && (
          <button className="button" onClick={onReject}>
            {isPlan ? 'Skip these datasets' : 'Reject weights'}
          </button>
        )}
        <button ref={approveRef} className="button primary big" onClick={onApprove}>
          <Icon name="check" />
          {isPlan ? 'Approve data plan' : 'Approve weights'}
        </button>
      </footer>
    </aside>
  )
}
