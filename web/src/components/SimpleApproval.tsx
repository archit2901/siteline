// Approvals in plain words for the customer view: a yes/no for the data, "looks right" or adjust for priorities.
import { useEffect, useRef, useState } from 'react'
import type { ApprovalRequest, ApproveRequest } from '../types/contracts'
import Icon from './Icon'
import WeightSliders from './WeightSliders'

interface Props {
  approval: ApprovalRequest
  featureLabels: Record<string, string>
  /** Replays continue as recorded: only the approve button is offered. */
  readOnly: boolean
  onAnswer: (answer: ApproveRequest) => void
}

const list = (items: string[]) => (items.length <= 1 ? items.join('') : `${items.slice(0, -1).join(', ')} and ${items.at(-1)}`)

export default function SimpleApproval({ approval, featureLabels, readOnly, onAnswer }: Props) {
  const yesRef = useRef<HTMLButtonElement>(null)
  const [adjusting, setAdjusting] = useState(false)
  const proposed = approval.spec?.weights ?? {}
  const [weights, setWeights] = useState<Record<string, number>>(proposed)
  useEffect(() => {
    setWeights(approval.spec?.weights ?? {})
    setAdjusting(false)
    yesRef.current?.focus()
  }, [approval.approval_id]) // eslint-disable-line react-hooks/exhaustive-deps
  const id = approval.approval_id

  if (approval.kind === 'data_plan') {
    const titles = (approval.candidates ?? []).map((c) => c.title)
    return (
      <section className="ask-card" aria-labelledby="ok-data">
        <h2 id="ok-data">{titles.length ? 'Can I use this data?' : 'Ready to continue'}</h2>
        <p>
          {titles.length
            ? `To measure what matters for your business, I'd like to use ${list(titles)}. Nothing is downloaded until you say yes.`
            : 'No extra data is needed; I will use the Census and city data I already have.'}
        </p>
        <div className="row">
          <button ref={yesRef} className="button primary big" onClick={() => onAnswer({ approval_id: id, decision: 'approve' })}>
            <Icon name="check" />
            {titles.length ? 'Yes, use it' : 'Continue'}
          </button>
          {!readOnly && titles.length > 0 && (
            <button className="button big" onClick={() => onAnswer({ approval_id: id, decision: 'reject' })}>
              Skip
            </button>
          )}
        </div>
      </section>
    )
  }

  const top = Object.entries(proposed)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 3)
    .map(([k]) => (featureLabels[k] ?? k.replace(/_/g, ' ')).toLowerCase())
  const edited = Object.keys(proposed).some((k) => Math.abs((weights[k] ?? 0) - proposed[k]) > 0.005)
  return (
    <section className="ask-card" aria-labelledby="ok-weights">
      <h2 id="ok-weights">Does this look right?</h2>
      <p>I'll focus most on {list(top)}.</p>
      {adjusting && (
        <WeightSliders
          weights={weights}
          proposed={proposed}
          rationale={(approval.spec?.rationale ?? {}) as Record<string, string>}
          featureLabels={featureLabels}
          onChange={setWeights}
        />
      )}
      <div className="row">
        <button
          ref={yesRef}
          className="button primary big"
          onClick={() =>
            onAnswer(edited && approval.spec ? { approval_id: id, decision: 'edit', edited_spec: { ...approval.spec, weights } } : { approval_id: id, decision: 'approve' })
          }
        >
          <Icon name="check" />
          {adjusting && edited ? 'Use these' : 'Looks right'}
        </button>
        {!readOnly && !adjusting && (
          <button className="button big" onClick={() => setAdjusting(true)}>
            Adjust
          </button>
        )}
      </div>
    </section>
  )
}
