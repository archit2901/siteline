import { useState } from 'react'
import presets from '../presets/presets.json'
import type { Office, PracticeProfile } from '../types/contracts'

export interface Preset {
  id: string
  label: string
  healthcare: boolean
  profile: PracticeProfile
}
export const PRESETS = presets as Preset[]

const TILE = [
  { bg: 'var(--sage)', shape: { borderRadius: '50%' } },
  { bg: 'var(--sky)', shape: { borderRadius: '0 0 999px 999px' } },
  { bg: 'var(--sand)', shape: { clipPath: 'polygon(0 0,100% 0,0 100%)' } },
  { bg: 'var(--lime)', shape: { transform: 'rotate(45deg) scale(.75)' } },
  { bg: 'var(--coral)', shape: { borderRadius: '999px 0 0 0' } },
  { bg: 'var(--steel)', shape: { borderRadius: '50%', transform: 'scale(.7)' } },
  { bg: 'var(--lime2)', shape: { clipPath: 'polygon(50% 0,100% 100%,0 100%)' } },
]
const SPECIALTIES: [string, string][] = [
  ['orthopedics', 'Orthopedics'],
  ['pediatric_dentistry', 'Pediatric dentistry'],
  ['dialysis', 'Dialysis'],
  ['physical_therapy', 'Physical therapy'],
]
const PAYERS: [string, string][] = [
  ['medicare', 'Medicare'],
  ['medicaid', 'Medicaid'],
  ['chip', 'CHIP'],
  ['commercial', 'Commercial'],
  ['workers_comp', "Workers' comp"],
]
const LANGS = ['English', 'Spanish', 'Vietnamese', 'Chinese', 'Arabic']
const PEOPLE_HINTS = ['Mostly retirees', 'Many come by bus', 'Spanish-speaking families', 'Kids and teens', 'Athletes and active adults',
  'Diabetes and high blood pressure', 'Injured workers', 'Referred by primary care', 'Apartment renters', 'Office workers at lunch']
const AREA_HINTS = ['On a bus line', 'Near schools', 'Near a hospital', 'Close to gyms and parks', 'Near senior living',
  'Near large employers', 'Next to a grocery store', 'Visible from a main road', 'Walkable, busy street', 'Growing new suburbs']

const NOTE_PREFIX = 'What would draw people to a location: '

/** The contract has `customers` and one `free_text`; the two boxes map onto them. */
function notesOf(p: PracticeProfile): [string, string] {
  const text = p.free_text ?? ''
  const i = text.indexOf(NOTE_PREFIX)
  if (i >= 0) return [p.customers || text.slice(0, i).trim(), text.slice(i + NOTE_PREFIX.length).trim()]
  return [[p.customers, text].filter(Boolean).join(' '), '']
}

interface Props {
  live: boolean
  hasRecording: (presetId: string) => boolean
  onRun: (profile: PracticeProfile, recorded?: string) => void
}

export default function Start({ live, hasRecording, onRun }: Props) {
  const [p, setP] = useState<PracticeProfile>(PRESETS[0].profile)
  const [[people, area], setNotes] = useState<[string, string]>(notesOf(PRESETS[0].profile))
  const [picked, setPicked] = useState<string | null>(PRESETS[0].id)

  const healthcare = Boolean(p.specialty) && !p.business_type
  const who = healthcare ? 'patients' : 'customers'
  const cons = p.constraints ?? { min_sqft: 2000, max_sqft: 8000 }
  const set = (patch: Partial<PracticeProfile>) => { setP({ ...p, ...patch }); setPicked(null) }
  const setC = (patch: Record<string, unknown>) => set({ constraints: { ...cons, ...patch } })
  const offices = p.existing_offices ?? []
  const setOffice = (i: number, patch: Partial<Office>) => set({ existing_offices: offices.map((o, j) => (j === i ? { ...o, ...patch } : o)) })
  const toggle = (list: string[], v: string) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v])
  const add = (cur: string, hint: string) => (cur.trim() ? `${cur.trim().replace(/[.]?$/, '.')} ${hint}.` : `${hint}.`)
  const num = (v: string) => (v === '' ? null : Number(v))
  const words = `${people} ${area}`.trim().split(/\s+/).filter(Boolean).length
  const strength = words === 0 ? 0 : words < 15 ? 1 : words < 40 ? 2 : 3

  const use = (preset: Preset) => { setP(preset.profile); setNotes(notesOf(preset.profile)); setPicked(preset.id) }
  const profile = (): PracticeProfile => ({
    ...p, customers: people.trim(), free_text: area.trim() ? `${NOTE_PREFIX}${area.trim()}` : '',
  })

  return (
    <div className="page">
      <div className="wrap">
        <section className="hero">
          <h1>Where should your next location open?</h1>
          <p>Tell us about your business. An agent works out what makes it succeed here, finds the data, and ranks real Houston spaces by the {who} they’d win.</p>
        </section>
      </div>
      <div className="frame">
        <span className="tag" style={{ left: 'calc(6% + 10px)', top: 10 }}>Houston</span>
        <span className="tag" style={{ right: 'calc(6% + 10px)', top: 10 }}>11,720 cells</span>
        <span className="tag" style={{ left: 'calc(6% + 10px)', bottom: 10 }}>Census · CMS · CDC · City of Houston</span>
        <span className="tag" style={{ right: 'calc(6% + 10px)', bottom: 10 }}>CoStar · Vexcel · Matterport</span>
        <span className="dot" style={{ left: '6%', top: 0 }} />
        <span className="dot" style={{ right: '6%', top: 0, transform: 'translate(50%,-50%)' }} />
        <span className="dot" style={{ left: '6%', top: '100%' }} />
        <span className="dot" style={{ right: '6%', top: '100%', transform: 'translate(50%,-50%)' }} />

        <div className="composer">
          <form className="form" onSubmit={(e) => { e.preventDefault(); onRun(profile()) }}>
            <section className="step">
              <span className="sn">1/4</span>
              <div className="sb">
                <h3>Your business</h3>
                <div className="grid2">
                  <label className="fl"><span>Name</span>
                    <input value={p.name} required onChange={(e) => set({ name: e.target.value })} placeholder="Gulf Coast Orthopedics" /></label>
                  <div className="fl"><span>Kind</span>
                    <div className="pills">
                      <button type="button" className={`pill ${healthcare ? 'on' : ''}`}
                        onClick={() => set({ specialty: p.specialty || SPECIALTIES[0][0], business_type: '', stock_pack: null })}>Healthcare practice</button>
                      <button type="button" className={`pill ${!healthcare ? 'on' : ''}`}
                        onClick={() => set({ specialty: '', business_type: p.business_type || '', payers_accepted: [], stock_pack: null })}>Any other business</button>
                    </div></div>
                </div>
                {healthcare
                  ? <label className="fl"><span>Specialty</span>
                      <select value={p.specialty} onChange={(e) => set({ specialty: e.target.value, stock_pack: null })}>
                        {SPECIALTIES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                      </select></label>
                  : <label className="fl"><span>What kind of business?</span>
                      <input value={p.business_type ?? ''} required placeholder="laundromat, tutoring center, coffee shop, gym…"
                        onChange={(e) => set({ business_type: e.target.value })} /></label>}
                <label className="fl"><span>Services</span>
                  <input value={(p.services ?? []).join(', ')} placeholder={healthcare ? 'joint replacement, sports medicine' : 'wash and fold, self-service'}
                    onChange={(e) => set({ services: e.target.value.split(',').map((s) => s.trimStart()) })} /></label>
                <div className="fl"><span>Existing locations</span>
                  {offices.map((o, i) => (
                    <div className="orow" key={i}>
                      <input value={o.name} placeholder="Name" onChange={(e) => setOffice(i, { name: e.target.value })} />
                      <input value={o.address ?? ''} placeholder={o.lat != null ? 'Pinned on map' : 'Street address'}
                        onChange={(e) => setOffice(i, { address: e.target.value, lat: null, lon: null })} />
                      <input type="number" min={1} value={o.clinicians ?? 1} title={healthcare ? 'Clinicians' : 'Staff'} onChange={(e) => setOffice(i, { clinicians: Number(e.target.value) })} />
                      <button type="button" className="x" aria-label="Remove location" onClick={() => set({ existing_offices: offices.filter((_, j) => j !== i) })}>×</button>
                    </div>
                  ))}
                  <button type="button" className="addl" onClick={() => set({ existing_offices: [...offices, { name: '', address: '', clinicians: 2 }] })}>+ Add a location</button>
                </div>
              </div>
            </section>

            <section className="step">
              <span className="sn">2/4</span>
              <div className="sb">
                <h3>Who you serve</h3>
                {healthcare && (
                  <div className="fl"><span>Insurance you accept</span>
                    <div className="pills">{PAYERS.map(([v, l]) => (
                      <button type="button" key={v} className={`pill ${(p.payers_accepted ?? []).includes(v) ? 'on' : ''}`}
                        aria-pressed={(p.payers_accepted ?? []).includes(v)} onClick={() => set({ payers_accepted: toggle(p.payers_accepted ?? [], v) })}>{l}</button>))}</div>
                  </div>
                )}
                <div className="fl"><span>Languages your staff speak</span>
                  <div className="pills">{LANGS.map((l) => (
                    <button type="button" key={l} className={`pill ${(p.languages ?? []).includes(l) ? 'on' : ''}`}
                      aria-pressed={(p.languages ?? []).includes(l)} onClick={() => set({ languages: toggle(p.languages ?? [], l) })}>{l}</button>))}</div>
                </div>
              </div>
            </section>

            <section className="step">
              <span className="sn">3/4</span>
              <div className="sb">
                <h3>The space you need</h3>
                <div className="grid4">
                  <label className="fl"><span>Min sq ft</span><input type="number" value={cons.min_sqft ?? ''} onChange={(e) => setC({ min_sqft: num(e.target.value) })} /></label>
                  <label className="fl"><span>Max sq ft</span><input type="number" value={cons.max_sqft ?? ''} onChange={(e) => setC({ max_sqft: num(e.target.value) })} /></label>
                  <label className="fl"><span>Max $/sf/yr</span><input type="number" value={cons.max_rent_psf ?? ''} onChange={(e) => setC({ max_rent_psf: num(e.target.value) })} /></label>
                  <label className="fl"><span>Parking /1,000 sf</span><input type="number" step={0.5} value={cons.min_parking_per_1000sf ?? ''} onChange={(e) => setC({ min_parking_per_1000sf: num(e.target.value) })} /></label>
                </div>
                <div className="fl"><span>Floor</span>
                  <div className="pills">
                    <button type="button" className={`pill ${cons.ground_floor !== false ? 'on' : ''}`} onClick={() => setC({ ground_floor: true })}>Ground floor only</button>
                    <button type="button" className={`pill ${cons.ground_floor === false ? 'on' : ''}`} onClick={() => setC({ ground_floor: false })}>Any floor</button>
                  </div></div>
              </div>
            </section>

            <section className="step feature">
              <span className="sn">4/4</span>
              <div className="sb">
                <div className="fhead">
                  <h3>What makes your {healthcare ? 'practice' : 'business'} different</h3>
                  <span className={`meter m${strength}`} title="More detail gives the agent more to work with"><i /><i /><i />
                    {['Add detail', 'A start', 'Good', 'Great detail'][strength]}</span>
                </div>
                <p className="lead">This is what turns a generic search into yours. The agent turns each detail into a factor, then goes looking for public data to measure it in every Houston neighborhood.</p>
                <label className="fl"><span>Your {who}</span>
                  <textarea rows={3} value={people} onChange={(e) => { setNotes([e.target.value, area]); setPicked(null) }}
                    placeholder={`Who are they, how do they get to you, ${healthcare ? 'who refers them?' : 'when do they come?'}`} /></label>
                <div className="hints">{PEOPLE_HINTS.map((h) => <button type="button" key={h} onClick={() => setNotes([add(people, h), area])}>+ {h}</button>)}</div>
                <label className="fl"><span>What about an area would pull people to your door?</span>
                  <textarea rows={3} value={area} onChange={(e) => { setNotes([people, e.target.value]); setPicked(null) }}
                    placeholder="The small things that make one neighborhood work and the next one not: what’s nearby, how people get around, who sends them your way." /></label>
                <div className="hints">{AREA_HINTS.map((h) => <button type="button" key={h} onClick={() => setNotes([people, add(area, h)])}>+ {h}</button>)}</div>
              </div>
            </section>

            <div className="form-foot">
              {picked && hasRecording(picked)
                ? <button type="button" className="linkbtn" onClick={() => onRun(profile(), picked)}>Play the recorded run instead</button>
                : <span className="foot-note">{live ? 'Runs the live agent.' : 'Replays a recorded run.'}</span>}
              <button className="cta big" type="submit"><span className="arrow">↗</span>
                <span className="label">{live ? 'Find sites' : 'Replay a recorded run'}</span></button>
            </div>
          </form>

          <div className="templates">
            <div className="tpl-label">Or start from</div>
            {PRESETS.map((preset, i) => (
              <button key={preset.id} className={`tpl ${picked === preset.id ? 'on' : ''}`} style={{ background: TILE[i % TILE.length].bg }} onClick={() => use(preset)}>
                <span className="shape" style={TILE[i % TILE.length].shape} /><b>{preset.label}</b>
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
