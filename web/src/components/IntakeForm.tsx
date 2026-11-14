import type { PracticeProfile } from '../types/contracts'
import presets from '../presets/presets.json'
import Icon from './Icon'

export interface Preset {
  id: string
  label: string
  healthcare: boolean
  profile: PracticeProfile
}
export const PRESETS = presets as Preset[]

export type Source = 'live' | 'recorded' | 'offline'

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

const SUBMIT: Record<Source, string> = {
  live: 'Find sites',
  recorded: 'Play the recorded run',
  offline: 'Play the offline demo',
}
const SUBMIT_HINT: Record<Source, string> = {
  live: 'Runs the agent live on Amazon Nova. You approve the data plan and the weights.',
  recorded: 'Replays a saved live run of this example from the server, pausing at approvals. No model calls.',
  offline: 'Replays a sample run in the browser with invented sites. Works with no server.',
}

const list = (v: string) => v.split(',').map((s) => s.trim()).filter(Boolean)

interface Props {
  profile: PracticeProfile
  presetId: string | null
  source: Source
  recordedIds: string[]
  onPreset: (preset: Preset) => void
  onChange: (profile: PracticeProfile) => void
  onSubmit: () => void
  /** Inside the chat's "Edit details": fields only, no heading, examples or run button. */
  embedded?: boolean
}

export default function IntakeForm({ profile, presetId, source, recordedIds, onPreset, onChange, onSubmit, embedded = false }: Props) {
  const cons = profile.constraints ?? { min_sqft: 2000, max_sqft: 8000 }
  const set = (patch: Partial<PracticeProfile>) => onChange({ ...profile, ...patch })
  const setCons = (patch: Record<string, unknown>) => set({ constraints: { ...cons, ...patch } })
  const payers = profile.payers_accepted ?? []
  const num = (v: string) => (v === '' ? null : Number(v))
  const healthcare = Boolean(profile.specialty) && !profile.business_type
  const editable = source === 'live'

  return (
    <form
      className="intake"
      onSubmit={(e) => {
        e.preventDefault()
        onSubmit()
      }}
    >
      {!embedded && (
      <section className="section">
        <h1>Where in Houston should this business open next?</h1>
        <p className="lede">Start from an example or describe any business. The agent works out who your customers are, finds the data, and ranks real spaces for lease.</p>
        <div className="presets" role="group" aria-label="Example businesses">
          {PRESETS.map((p) => (
            <button key={p.id} type="button" className="preset" data-health={p.healthcare} aria-pressed={p.id === presetId} onClick={() => onPreset(p)}>
              <span className="ring" aria-hidden="true" />
              {p.label}
              {recordedIds.includes(p.id) && <span className="rec" title="A recorded run of this example is available">Recorded</span>}
            </button>
          ))}
        </div>
      </section>
      )}

      <fieldset className="section form" disabled={!editable}>
        {!editable && <p className="hint">{source === 'recorded' ? 'Recorded runs replay a saved search, so the form is read-only. Switch to Live agent to edit it.' : 'The offline demo replays a sample run. Switch to Live agent to run this form.'}</p>}
        <fieldset>
          <legend>The business</legend>
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
              <div>
                <span className="hint">Insurance accepted</span>
                <div className="payers" style={{ marginTop: 6 }}>
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
                </div>
              </div>
            </>
          ) : (
            <label>
              What kind of business?
              <input value={profile.business_type ?? ''} placeholder="laundromat, tutoring center, gym, bakery" onChange={(e) => set({ business_type: e.target.value })} required />
            </label>
          )}
          <label>
            Who are your customers?
            <input value={profile.customers ?? ''} placeholder="apartment renters without in-unit washers" onChange={(e) => set({ customers: e.target.value })} />
          </label>
          <label>
            Services
            <input value={(profile.services ?? []).join(', ')} placeholder="wash and fold, self-service" onChange={(e) => set({ services: list(e.target.value) })} />
          </label>
          <label>
            Languages spoken by staff
            <input value={(profile.languages ?? []).join(', ')} onChange={(e) => set({ languages: list(e.target.value) })} />
          </label>
        </fieldset>

        {(profile.existing_offices ?? []).length > 0 && (
          <fieldset>
            <legend>Your existing locations</legend>
            <p className="hint">New sites are scored after these take their share of customers.</p>
            <ul className="offices">
              {(profile.existing_offices ?? []).map((o) => (
                <li key={o.name}>
                  {o.name}
                  {healthcare && o.clinicians ? `, ${o.clinicians} clinicians` : ''}
                </li>
              ))}
            </ul>
          </fieldset>
        )}

        <fieldset>
          <legend>The space</legend>
          <div className="pair">
            <label>
              Smallest (sq ft)
              <input type="number" value={cons.min_sqft ?? ''} onChange={(e) => setCons({ min_sqft: num(e.target.value) })} />
            </label>
            <label>
              Largest (sq ft)
              <input type="number" value={cons.max_sqft ?? ''} onChange={(e) => setCons({ max_sqft: num(e.target.value) })} />
            </label>
          </div>
          <div className="pair">
            <label>
              Max rent ($/sq ft/yr)
              <input type="number" value={cons.max_rent_psf ?? ''} placeholder="Any" onChange={(e) => setCons({ max_rent_psf: num(e.target.value) })} />
            </label>
            <label>
              Parking per 1,000 sq ft
              <input type="number" step="0.5" value={cons.min_parking_per_1000sf ?? ''} onChange={(e) => setCons({ min_parking_per_1000sf: num(e.target.value) })} />
            </label>
          </div>
          <label className="check">
            <input type="checkbox" checked={cons.ground_floor ?? true} onChange={(e) => setCons({ ground_floor: e.target.checked })} />
            Ground floor required
          </label>
        </fieldset>

        <fieldset>
          <legend>Anything else</legend>
          <label>
            Notes for the agent
            <textarea rows={3} value={profile.free_text ?? ''} placeholder="What has worked at your other locations?" onChange={(e) => set({ free_text: e.target.value })} />
          </label>
        </fieldset>
      </fieldset>

      {!embedded && (
      <div className="go">
        <button className="button primary big block" type="submit">
          {source !== 'live' && <Icon name="play" />}
          {SUBMIT[source]}
        </button>
        <p className="hint">{SUBMIT_HINT[source]}</p>
      </div>
      )}
    </form>
  )
}
