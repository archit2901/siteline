import { useEffect, useRef, useState } from 'react'
import { safeHref } from '../lib/safe'
import type { ApprovalRequest, ApproveRequest } from '../types/contracts'
import Icon from './Icon'
import WeightSliders from './WeightSliders'

interface Props {
  approval: ApprovalRequest
  featureLabels: Record<string, string>
  /** Offline demo: the replay continues either way, so only Approve is offered. */
  readOnly: boolean
  recorded: boolean
  onApprove: (answer: ApproveRequest) => void
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
  const proposed = approval.spec?.weights ?? {}
  const rationale = (approval.spec?.rationale ?? {}) as Record<string, string>
  const [weights, setWeights] = useState<Record<string, number>>(proposed)
  useEffect(() => setWeights(approval.spec?.weights ?? {}), [approval.approval_id]) // eslint-disable-line react-hooks/exhaustive-deps
  const edited = Object.keys(proposed).some((k) => Math.abs((weights[k] ?? 0) - proposed[k]) > 0.005)

  const approve = () => {
    if (isPlan || !edited || !approval.spec) return onApprove({ approval_id: approval.approval_id, decision: 'approve' })
    onApprove({ approval_id: approval.approval_id, decision: 'edit', edited_spec: { ...approval.spec, weights } })
  }

  return (
    <aside className="approval" role="region" aria-labelledby="approval-title">
      <header>
        <span className="stop" aria-hidden="true" />
        <div>
          <h2 id="approval-title">{isPlan ? 'Approve the data plan' : 'Approve the weights'}</h2>
          <p className="lede">
            {isPlan
              ? 'Public datasets the scouts found for factors our stock data does not cover. Nothing is downloaded until you approve.'
              : readOnly
                ? 'How much each factor counts toward a zone’s fit, weighed for this business. Every color on the map comes from these weights.'
                : 'How much each factor counts toward a zone’s fit, weighed for this business. Drag a slider to change it; the shares always add up to 100%.'}
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
          <WeightSliders weights={weights} proposed={proposed} rationale={rationale} featureLabels={featureLabels} readOnly={readOnly} onChange={setWeights} />
        )}
      </div>

      <footer>
        {(readOnly || recorded) && <span className="hint">{recorded ? 'Recorded run: the saved run continues as it was approved.' : 'Offline demo: approving continues the replay.'}</span>}
        {!readOnly && (
          <button className="button" onClick={onReject}>
            {isPlan ? 'Skip these datasets' : 'Reject weights'}
          </button>
        )}
        <button ref={approveRef} className="button primary big" onClick={approve}>
          <Icon name="check" />
          {isPlan ? 'Approve data plan' : edited ? 'Approve my weights' : 'Approve weights'}
        </button>
      </footer>
    </aside>
  )
}
