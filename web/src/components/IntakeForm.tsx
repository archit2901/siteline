import type { PracticeProfile } from '../types/contracts'
import presets from '../presets/presets.json'

export interface Preset {
  id: string
  label: string
  healthcare: boolean
  profile: PracticeProfile
}
export const PRESETS = presets as Preset[]

const PAYERS: [string, string][] = [
  ['medicare', 'Medicare'],
  ['medicaid', 'Medicaid'],
  ['chip', 'CHIP'],
  ['commercial', 'Commercial'],
  ['workers_comp', "Workers' comp"],
]
const SPECIALTIES: [string, string][] = [
  ['orthopedics', 'Orthopedics'],
  ['pediatric_dentistry', 'Pediatric dentistry'],
  ['dialysis', 'Dialysis'],
  ['physical_therapy', 'Physical therapy'],
]

interface Props {
  profile: PracticeProfile
  presetId: string | null
  fixtureMode: boolean
  onPreset: (preset: Preset) => void
  onChange: (profile: PracticeProfile) => void
  onSubmit: () => void
  /** Set when agent-api has a recorded run of the selected example. */
  onReplay?: () => void
}

export default function IntakeForm({ profile, presetId, fixtureMode, onPreset, onChange, onSubmit, onReplay }: Props) {
  const cons = profile.constraints ?? { min_sqft: 2000, max_sqft: 8000 }
  const set = (patch: Partial<PracticeProfile>) => onChange({ ...profile, ...patch })
  const setCons = (patch: Record<string, unknown>) => set({ constraints: { ...cons, ...patch } })
  const payers = profile.payers_accepted ?? []
  const num = (v: string) => (v === '' ? null : Number(v))
  const healthcare = Boolean(profile.specialty) && !profile.business_type

  return (
    <section className="section">
      <h2>Where in Houston should this business open next?</h2>
      <p className="hint">Start from an example or describe any business.</p>
      <div className="presets" role="group" aria-label="Example businesses" style={{ marginTop: 12 }}>
        {PRESETS.map((p) => (
          <button key={p.id} className="button" aria-pressed={p.id === presetId} onClick={() => onPreset(p)}>
            {p.label}
          </button>
        ))}
      </div>

      <form
        className="form"
        onSubmit={(e) => {
          e.preventDefault()
          onSubmit()
        }}
      >
        <label>
          Business name
          <input value={profile.name} onChange={(e) => set({ name: e.target.value })} required />
        </label>
        <label className="check">
          <input
            type="checkbox"
            checked={healthcare}
            onChange={(e) =>
              set(e.target.checked
                ? { specialty: SPECIALTIES[0][0], business_type: '', stock_pack: null }
                : { specialty: '', business_type: profile.business_type || '', payers_accepted: [], stock_pack: null })
            }
          />
          Healthcare practice
        </label>
        {healthcare ? (
          <>
            <label>
              Specialty
              <select value={profile.specialty} onChange={(e) => set({ specialty: e.target.value, stock_pack: null })}>
                {SPECIALTIES.map(([v, l]) => (
                  <option key={v} value={v}>
                    {l}
                  </option>
                ))}
              </select>
            </label>
            <fieldset>
              <legend>Insurance accepted</legend>
              {PAYERS.map(([v, l]) => (
                <label key={v} className="check">
                  <input
                    type="checkbox"
                    checked={payers.includes(v)}
                    onChange={(e) => set({ payers_accepted: e.target.checked ? [...payers, v] : payers.filter((p) => p !== v) })}
                  />
                  {l}
                </label>
              ))}
            </fieldset>
          </>
        ) : (
          <label>
            What kind of business?
            <input
              value={profile.business_type ?? ''}
              placeholder="laundromat, tutoring center, coffee shop, gym, ..."
              onChange={(e) => set({ business_type: e.target.value })}
              required
            />
          </label>
        )}
        <label>
          Who are your customers?
          <input
            value={profile.customers ?? ''}
            placeholder="e.g. apartment renters without in-unit washers"
            onChange={(e) => set({ customers: e.target.value })}
          />
        </label>
        <label>
          Languages spoken by staff
          <input
            value={(profile.languages ?? []).join(', ')}
            onChange={(e) => set({ languages: e.target.value.split(',').map((s) => s.trim()).filter(Boolean) })}
          />
        </label>
        <div>
          <span className="hint">Existing locations</span>
          <ul className="offices">
            {(profile.existing_offices ?? []).map((o) => (
              <li key={o.name}>
                {o.name}
                {healthcare && o.clinicians ? `, ${o.clinicians} clinicians` : ''}
              </li>
            ))}
          </ul>
        </div>
        <div className="pair">
          <label>
            Smallest space (sq ft)
            <input type="number" value={cons.min_sqft ?? ''} onChange={(e) => setCons({ min_sqft: num(e.target.value) })} />
          </label>
          <label>
            Largest space (sq ft)
            <input type="number" value={cons.max_sqft ?? ''} onChange={(e) => setCons({ max_sqft: num(e.target.value) })} />
          </label>
        </div>
        <div className="pair">
          <label>
            Max rent ($/sq ft/yr)
            <input type="number" value={cons.max_rent_psf ?? ''} onChange={(e) => setCons({ max_rent_psf: num(e.target.value) })} />
          </label>
          <label>
            Parking per 1,000 sq ft
            <input
              type="number"
              step="0.5"
              value={cons.min_parking_per_1000sf ?? ''}
              onChange={(e) => setCons({ min_parking_per_1000sf: num(e.target.value) })}
            />
          </label>
        </div>
        <label className="check">
          <input type="checkbox" checked={cons.ground_floor ?? true} onChange={(e) => setCons({ ground_floor: e.target.checked })} />
          Ground floor required
        </label>
        <label>
          Anything else the agent should know
          <textarea rows={3} value={profile.free_text ?? ''} onChange={(e) => set({ free_text: e.target.value })} />
        </label>
        <button className="button primary" type="submit">
          {fixtureMode ? 'Replay a recorded run' : 'Find sites'}
        </button>
        {onReplay && (
          <button className="button" type="button" onClick={onReplay}>
            Play the recorded run
          </button>
        )}
        {onReplay && <p className="hint">The recorded run replays a saved live search of this example, without calling the models.</p>}
        {fixtureMode && (
          <p className="hint">Recorded run replays a saved search. Switch to Live agent to run this form for any business.</p>
        )}
      </form>
    </section>
  )
}
