import { useEffect, useRef, useState } from 'react'
import { SourceBadge, SourceLegend, useSource } from '../lib/expert'
import { safeHref } from '../lib/safe'
import type { Scout, Todo, TraceLine } from '../state/runReducer'
import type { ApprovalRequest, ApproveRequest, Factor } from '../types/contracts'

const STATUS: Record<string, string> = { covered: 'stock data', searching: 'searching', found: 'found online', proxy: 'proxy', dropped: 'dropped' }
const SCOUT: Record<Scout['status'], string> = {
  running: 'searching', retrying: 'trying another source', ready: 'dataset ready', found: 'found', proxy: 'using a proxy', dropped: 'dropped',
}

/** The run's steps. Under "Reason about what makes a location succeed", the analyst's ideas appear as it writes them. */
export function Plan({ todos, ideas = [] }: { todos: Todo[]; ideas?: TraceLine[] }) {
  const listRef = useRef<HTMLOListElement>(null)
  useEffect(() => { listRef.current?.scrollTo({ top: listRef.current.scrollHeight, behavior: 'smooth' }) }, [ideas.length])
  return (
    <div>
      {todos.map((t, i) => (
        <div key={t.content}>
          <div className={`todo ${t.status}`}>
            <span className="n">{i + 1}/{todos.length}</span>
            <span className="t">{t.content}</span>
          </div>
          {/^reason/i.test(t.content) && ideas.length > 0 && (t.status === 'in_progress' ? (
            <ol className="ideas" ref={listRef} aria-live="polite">
              {ideas.map((l) => <li key={l.seq} title={l.text}>{l.text.split(':')[0]}</li>)}
            </ol>
          ) : (
            <details className="ideas-done">
              <summary>Considered {ideas.length} ideas</summary>
              <ol className="ideas">{ideas.map((l) => <li key={l.seq} title={l.text}>{l.text.split(':')[0]}</li>)}</ol>
            </details>
          ))}
        </div>
      ))}
    </div>
  )
}

function FactorRow({ f, scout }: { f: Factor; scout?: Scout }) {
  const [open, setOpen] = useState(false)
  const status = f.status ?? 'searching'
  return (
    <div className={`factor st-${status}`}>
      <button onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="sq" />
        <span>{f.factor} <span className="dirm">{f.direction > 0 ? '↑' : '↓'}</span> <SourceBadge factorId={f.id} featureId={f.feature_id} /></span>
        <span className="stt">{scout && !['found', 'proxy', 'dropped'].includes(scout.status) ? SCOUT[scout.status] : STATUS[status]}</span>
      </button>
      {open && (
        <div className="body">
          <span><b>Why</b> · {f.why}</span>
          <span><b>Measured by</b> · {f.proxy}</span>
          {scout && <span><b>Scout</b> · {SCOUT[scout.status]}{scout.tries > 0 ? `, try ${scout.tries} of 3` : ''}{scout.last ? ` · ${scout.last}` : ''}</span>}
          {f.caveat && <span className="warn">{f.caveat}</span>}
        </div>
      )}
    </div>
  )
}

export function Factors({ factors, scouts }: { factors: Factor[]; scouts: Record<string, Scout> }) {
  if (!factors.length) return null
  const n = (s: string) => factors.filter((f) => (f.status ?? 'searching') === s).length
  return (
    <>
      <div className="kicker"><span>Success factors</span>
        <span className="mono">{n('covered')} stock · {n('found')} found · {n('proxy')} proxy · {n('dropped')} dropped</span></div>
      {factors.map((f) => <FactorRow key={f.id} f={f} scout={scouts[f.id]} />)}
    </>
  )
}

export function Approval({ approval, featureLabels, readOnly, step, businessName, onApprove, onReject, preset }: {
  approval: ApprovalRequest; featureLabels: Record<string, string>; readOnly: boolean; step: string; businessName: string
  /** weights tuned on a previous run (keys without the run suffix), pre-set on the sliders */
  preset?: Record<string, number> | null
  onApprove: (decision: ApproveRequest) => void; onReject?: () => void
}) {
  const ref = useRef<HTMLButtonElement>(null)
  useEffect(() => ref.current?.focus(), [approval.approval_id])
  const isPlan = approval.kind === 'data_plan'
  const factors = approval.factors ?? []
  const proposed = approval.spec?.weights ?? {}
  const rationale = (approval.spec?.rationale ?? {}) as Record<string, string>
  const order = Object.entries(proposed).sort((a, b) => b[1] - a[1]).map(([fid]) => fid)
  const initial = () => {
    const base = approval.spec?.weights ?? {}
    if (!preset) return base
    return Object.fromEntries(Object.entries(base).map(([fid, v]) => [fid, preset[fid.replace(/_[0-9a-f]{6}$/, '')] ?? v]))
  }
  const [w, setW] = useState<Record<string, number>>(initial)
  useEffect(() => setW(initial()), [approval.approval_id]) // eslint-disable-line react-hooks/exhaustive-deps
  const total = Object.values(w).reduce((a, b) => a + b, 0)
  const edited = !isPlan && order.some((fid) => Math.abs((w[fid] ?? 0) / (total || 1) - proposed[fid]) > 0.005)
  const share = (fid: string) => (total > 0 ? (w[fid] ?? 0) / total : 0)
  const top = Math.max(0.5, ...Object.values(proposed).map((x) => Math.min(1, x * 2.5)))
  const factorFor = (fid: string) => factors.find((f) => f.feature_id === fid)

  const approve = () => {
    if (!edited || !approval.spec) return onApprove({ approval_id: approval.approval_id, decision: 'approve' })
    // Weights are normalized to sum to 1, as ScoringSpec requires; everything else in the spec is unchanged.
    const weights = Object.fromEntries(order.map((fid) => [fid, Math.round(share(fid) * 10000) / 10000]))
    onApprove({ approval_id: approval.approval_id, decision: 'edit', edited_spec: { ...approval.spec, weights } })
  }

  if (isPlan) {
    return (
      <div className="approval" role="dialog" aria-labelledby="approval-title">
        <div>
          <span className="hand">{step}</span>
          <h3 id="approval-title">Approve the data plan</h3>
          <p className="count">These public datasets would measure what our stock data can’t. Nothing is downloaded until you approve.</p>
          <button ref={ref} className="cta" onClick={approve}><span className="arrow">✓</span><span className="label">Approve</span></button>
          {onReject && !readOnly && <button className="linkbtn" onClick={onReject}>Skip these datasets</button>}
        </div>
        <div>
          {(approval.candidates ?? []).map((c) => {
            const href = safeHref(c.url)
            return (
              <div className="prow" key={c.id}>
                <span className="pf">{factors.find((f) => f.id === c.factor_id)?.factor ?? c.factor_id}</span>
                <span>{href ? <a href={href} target="_blank" rel="noreferrer">{c.title}</a> : c.title}<em> · {c.publisher}</em></span>
                <span className={`pchk ${c.status === 'invalid' ? 'bad' : ''}`}>
                  {c.status === 'invalid' ? 'not usable' : c.status === 'found_not_ingested' ? 'found, not ingestible' : 'ready'}</span>
              </div>
            )
          })}
        </div>
      </div>
    )
  }

  return (
    <div className="approval weights" role="dialog" aria-labelledby="approval-title">
      <div>
        <span className="hand">{step}</span>
        <h3 id="approval-title">Here’s what I think will contribute to success</h3>
        <p className="count">Weighed for {businessName}. Drag to change what each factor is worth.</p>
        <button ref={ref} className="cta" onClick={approve} disabled={total <= 0}>
          <span className="arrow">✓</span><span className="label">{edited ? 'Use my weights' : 'Looks right'}</span></button>
        {edited && <button className="linkbtn" onClick={() => setW(proposed)}>Reset to my suggestion</button>}
      </div>
      <div>
        <SourceLegend />
        {order.map((fid) => {
          const f = factorFor(fid)
          const label = f?.factor ?? featureLabels[fid] ?? fid
          const changed = Math.abs(share(fid) - proposed[fid]) > 0.005
          return (
            <div className={`wrow edit ${changed ? 'changed' : ''}`} key={fid}>
              <span title={label}>{label} <SourceBadge factorId={f?.id} featureId={fid} /></span>
              <input type="range" min={0} max={top} step={0.005} value={w[fid] ?? 0} aria-label={`Weight for ${label}`}
                onChange={(e) => setW({ ...w, [fid]: Number(e.target.value) })} />
              <span className="pct">{Math.round(share(fid) * 100)}%
                {changed && <em className="was">was {Math.round(proposed[fid] * 100)}%</em>}</span>
              <WhyLine fid={fid} factorId={f?.id} why={rationale[fid]} />
            </div>
          )
        })}
      </div>
    </div>
  )
}

/** Under a weight: the expert rule that backs it, else the model's reason. */
function WhyLine({ fid, factorId, why }: { fid: string; factorId?: string; why?: string }) {
  const s = useSource({ factorId, featureId: fid })
  if (s?.source === 'expert' && s.rule) return <span className="wwhy expert">{s.rule}</span>
  return why ? <span className="wwhy">{why}</span> : null
}

export function LogDrawer({ trace, onClose }: { trace: TraceLine[]; onClose: () => void }) {
  return (
    <div className="drawer" role="dialog" aria-label="Agent log">
      <h4>Agent log <button onClick={onClose}>close</button></h4>
      {trace.map((l) => (
        <div className="ev" key={`${l.seq}-${l.kind}`}>
          <span className="ag">{l.agent} › </span>
          <span className={l.ok ? (l.kind === 'result' ? 'ok' : '') : 'bad'}>{l.kind === 'call' ? `runs ${l.tool}` : l.tool}</span>
          {l.text && <div className={l.kind === 'call' ? 'ag' : ''}>{l.text}</div>}
        </div>
      ))}
    </div>
  )
}
