/** Factor weights as shares of 100%, with the market-modeler's reason under each. Editable unless readOnly. */
interface Props {
  weights: Record<string, number>
  proposed: Record<string, number>
  rationale?: Record<string, string>
  featureLabels: Record<string, string>
  readOnly?: boolean
  onChange?: (weights: Record<string, number>) => void
}

/** Set one factor's share and rescale the others so the weights still sum to 1. */
export function setShare(weights: Record<string, number>, key: string, share: number): Record<string, number> {
  const v = Math.min(0.95, Math.max(0, share))
  const rest = Object.entries(weights).filter(([k]) => k !== key)
  const restTotal = rest.reduce((a, [, w]) => a + w, 0)
  const out: Record<string, number> = { [key]: v }
  for (const [k, w] of rest) out[k] = restTotal > 0 ? (w / restTotal) * (1 - v) : (1 - v) / rest.length
  return Object.fromEntries(Object.entries(out).map(([k, w]) => [k, Math.round(w * 10000) / 10000]))
}

export default function WeightSliders({ weights, proposed, rationale = {}, featureLabels, readOnly = false, onChange }: Props) {
  const order = Object.keys(proposed).sort((a, b) => proposed[b] - proposed[a])
  const max = Math.max(...Object.values(weights), 0.0001)
  return (
    <ul className="weights">
      {order.map((fid) => {
        const label = featureLabels[fid] ?? fid
        const w = weights[fid] ?? 0
        return (
          <li className="weight" key={fid}>
            <span className="wname">
              {label}
              {rationale[fid] && <small>{rationale[fid]}</small>}
            </span>
            {readOnly || !onChange ? (
              <span className="wbar" aria-hidden="true">
                <span style={{ width: `${(w / max) * 100}%` }} />
              </span>
            ) : (
              <input
                className="wslider"
                type="range"
                min={0}
                max={0.6}
                step={0.01}
                value={w}
                aria-label={`Weight for ${label}`}
                style={{ ['--fill' as string]: `${(w / 0.6) * 100}%` }}
                onChange={(e) => onChange(setShare(weights, fid, Number(e.target.value)))}
              />
            )}
            <span className="pct">{Math.round(w * 100)}%</span>
          </li>
        )
      })}
    </ul>
  )
}
