import { useEffect, useRef } from 'react'
import type { ApprovalRequest } from '../types/contracts'

interface Props {
  approval: ApprovalRequest
  featureLabels: Record<string, string>
  readOnly: boolean
  onApprove: () => void
}

export default function ApprovalModal({ approval, featureLabels, readOnly, onApprove }: Props) {
  const approveRef = useRef<HTMLButtonElement>(null)
  useEffect(() => approveRef.current?.focus(), [approval.approval_id])

  const isPlan = approval.kind === 'data_plan'
  const candidates = approval.candidates ?? []
  const factors = approval.factors ?? []
  const weights = Object.entries(approval.spec?.weights ?? {}).sort((a, b) => b[1] - a[1])
  const maxWeight = Math.max(...weights.map(([, w]) => w), 0.0001)

  return (
    <div className="modal-backdrop" role="presentation">
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="approval-title">
        <h2 id="approval-title">{isPlan ? 'Approve the data plan' : 'Approve the weights'}</h2>
        <p className="lede">
          {isPlan
            ? 'These public datasets would measure the factors our stock data does not cover. Nothing is downloaded until you approve.'
            : 'How much each factor counts toward a zone’s score, from the priorities the agent set. Every number on the map comes from these weights.'}
        </p>

        {isPlan ? (
          <table className="plan">
            <thead>
              <tr>
                <th>Factor</th>
                <th>Dataset</th>
                <th>Check</th>
              </tr>
            </thead>
            <tbody>
              {candidates.map((c) => (
                <tr key={c.id}>
                  <td>{factors.find((f) => f.id === c.factor_id)?.factor ?? c.factor_id}</td>
                  <td>
                    <a href={c.url} target="_blank" rel="noreferrer">
                      {c.title}
                    </a>
                    <div className="hint">{c.publisher}</div>
                  </td>
                  <td>{c.status === 'invalid' ? 'Not usable, replaced' : c.status === 'found_not_ingested' ? 'Found, not ingestible' : 'Ready to ingest'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="weights">
            {weights.map(([feature, w]) => (
              <div className="weight" key={feature}>
                <span>{featureLabels[feature] ?? feature}</span>
                <span className="bar" aria-hidden="true">
                  <span style={{ width: `${(w / maxWeight) * 100}%` }} />
                </span>
                <span className="tabular">{Math.round(w * 100)}%</span>
              </div>
            ))}
          </div>
        )}

        <div className="actions">
          {readOnly && <span className="hint">Recorded run: approving continues the replay.</span>}
          <button ref={approveRef} className="button primary" onClick={onApprove}>
            {isPlan ? 'Approve data plan' : 'Approve weights'}
          </button>
        </div>
      </div>
    </div>
  )
}
