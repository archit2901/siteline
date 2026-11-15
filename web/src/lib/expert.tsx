// expert.json (services/agent/expert.py): which factors an orthopedic expert rule backs, and which come from the
// model's general knowledge. Shared through context so every list of factors or weights can badge them.
import { createContext, useContext } from 'react'

export interface FactorSource { factor_id: string; feature_id: string | null; source: 'expert' | 'ai'; rules: string[]; tier: number }
export interface ExpertReport {
  functions: Record<string, number>
  org_type: string
  rules: Record<string, string>
  factors: FactorSource[]
  space: Record<string, number>
}

export const ExpertContext = createContext<ExpertReport | null>(null)

/** The provenance of a factor, by factor id or by the feature id it is measured with (run suffix ignored). */
export function useSource(key: { factorId?: string; featureId?: string | null }): { source: 'expert' | 'ai'; rule?: string } | null {
  const report = useContext(ExpertContext)
  if (!report) return null
  const base = (s?: string | null) => (s ?? '').replace(/_[0-9a-f]{6}$/, '')
  const row = report.factors.find((f) => (key.factorId && f.factor_id === key.factorId)
    || (key.featureId && (f.feature_id === key.featureId || (f.feature_id && base(f.feature_id) === base(key.featureId)))))
  if (!row) return null
  return { source: row.source, rule: row.source === 'expert' ? row.rules.map((r) => report.rules[r]).find(Boolean) : undefined }
}

export function SourceBadge({ factorId, featureId }: { factorId?: string; featureId?: string | null }) {
  const s = useSource({ factorId, featureId })
  if (!s) return null
  return s.source === 'expert'
    ? <span className="src expert" title={s.rule ?? 'Backed by an orthopedic expert rule'}>Expert</span>
    : <span className="src ai" title="From the model's general knowledge">AI</span>
}

/** The expert's rules that shaped this run, in their words. */
export function RuleBox({ title = 'Orthopedic expert rules in this run' }: { title?: string }) {
  const report = useContext(ExpertContext)
  const rules = Object.values(report?.rules ?? {})
  if (!rules.length) return null
  return (
    <div className="rulebox">
      <h4>{title}</h4>
      <ul>{rules.map((r) => <li key={r}>{r}</li>)}</ul>
    </div>
  )
}

export function SourceLegend() {
  const report = useContext(ExpertContext)
  if (!report?.factors.length) return null
  const n = report.factors.filter((f) => f.source === 'expert').length
  return (
    <div className="srclegend">
      <span><span className="src expert">Expert</span> orthopedic domain rule ({n})</span>
      <span><span className="src ai">AI</span> general model knowledge ({report.factors.length - n})</span>
    </div>
  )
}
