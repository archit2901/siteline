import { useEffect, useMemo, useRef, useState } from 'react'
import { intakeChat, intakeQuestions, intakeWebsite, type IntakeQuestion, type QuestionSet } from '../api/client'
import { FIRST_LOCATION, PAYER_IDS, PAYER_LABELS, composeFreeText, missingFor, parseAnswers } from '../lib/intake'
import presets from '../presets/presets.json'
import type { ChatMessage, Office, PracticeProfile } from '../types/contracts'

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
const SPECIALTIES = ['community health center', 'orthopedics', 'pediatric dentistry', 'dialysis', 'physical therapy', 'primary care', 'urgent care',
  'cardiology', 'dermatology', 'optometry', 'behavioral health', 'chiropractic']
const PACKED = new Set(['orthopedics', 'pediatric_dentistry', 'dialysis', 'physical_therapy', 'community_health'])
const LANGS = ['English', 'Spanish', 'Vietnamese', 'Chinese', 'Arabic']
const KIND_HINTS = ['food pantry', 'laundromat', 'pediatric dentist', 'daycare', 'coffee shop', 'boxing gym', 'nonprofit youth center']
const AREA_HINTS = ['On a bus line', 'Near schools', 'Near churches', 'Next to a grocery store', 'Near large employers',
  'Visible from a main road', 'Walkable, busy street', 'Lots of apartments', 'Growing new suburbs', 'Near a hospital']
/** Shown when the agent-api is unreachable (recorded mode): the questions every business gets. */
const OFFLINE: QuestionSet = {
  sector: 'general.base', label: 'Any business', sector_questions: [],
  base: [
    { id: 'locations', kind: 'locations', text: 'Where are your current locations?', why: 'Your own sites teach the agent what works.', options: [], placeholder: '', required: true, field: 'existing_offices' },
    { id: 'customers', kind: 'long', text: 'Who are your customers?', why: 'Picks who we count as demand.', options: [], placeholder: '', required: true, field: 'customers' },
    { id: 'pull', kind: 'long', text: 'What about a neighborhood would pull people to your door?', why: 'Local factors the scouts go find data for.', options: [], placeholder: '', required: false, field: 'free_text' },
  ],
}
const STEP_OF: Record<string, 'locations' | 'customers' | 'edge' | 'space'> = {
  locations: 'locations', best_location: 'locations', customers: 'customers', customer_origin: 'customers', access: 'customers',
  unique: 'edge', pull: 'edge', competitors: 'edge', space: 'space', visibility: 'space', tenure: 'space',
}

const isHealthcare = (p: PracticeProfile) => Boolean(p.specialty) && !p.business_type
const kindOf = (p: PracticeProfile) => (isHealthcare(p) ? (p.specialty ?? '').replace(/_/g, ' ') : p.business_type ?? '')

interface Props {
  live: boolean
  hasRecording: (presetId: string) => boolean
  onRun: (profile: PracticeProfile, recorded?: string) => void
}

export default function Start({ live, hasRecording, onRun }: Props) {
  const [p, setP] = useState<PracticeProfile>(PRESETS[0].profile)
  const [answers, setAnswers] = useState<Record<string, string>>(() => parseAnswers(PRESETS[0].profile.free_text).answers)
  const [notes, setNotes] = useState(() => parseAnswers(PRESETS[0].profile.free_text).notes)
  const [picked, setPicked] = useState<string | null>(PRESETS[0].id)
  const [qs, setQs] = useState<QuestionSet>(OFFLINE)
  const [mode, setMode] = useState<'form' | 'chat'>('form')
  const [tried, setTried] = useState(false)
  const [site, setSite] = useState('')
  const [siteMsg, setSiteMsg] = useState<string | null>(null)
  const [reading, setReading] = useState(false)
  const readSite = async () => {
    if (!site.trim() || reading) return
    setReading(true); setSiteMsg('Reading your website…')
    try {
      const res = await intakeWebsite(site.trim(), kindOf(p) ? p : { ...p, business_type: '', specialty: '', name: '' })
      const parsed = parseAnswers(res.profile.free_text)
      setP(res.profile); setAnswers(parsed.answers); setNotes(parsed.notes); setPicked(null); setSiteMsg(res.reply)
    } catch {
      setSiteMsg('Couldn’t reach the agent to read the site. Fill in the questions below instead.')
    } finally { setReading(false) }
  }

  const healthcare = isHealthcare(p)
  const kind = kindOf(p)
  const who = healthcare ? 'patients' : qs.sector.startsWith('nonprofit') ? 'clients' : 'customers'
  const cons = p.constraints ?? { min_sqft: 2000, max_sqft: 8000 }
  const offices = p.existing_offices ?? []
  const first = answers.locations === FIRST_LOCATION

  // The question set follows what the owner typed (debounced): "food pantry" pops up the food bank questions.
  useEffect(() => {
    if (!live) return
    const t = setTimeout(() => {
      intakeQuestions(p.business_type ?? '', healthcare ? p.specialty ?? '' : '').then(setQs).catch(() => setQs(OFFLINE))
    }, 350)
    return () => clearTimeout(t)
  }, [live, p.business_type, p.specialty, healthcare])

  const all = useMemo(() => [...qs.base, ...qs.sector_questions], [qs])
  const missing = missingFor(p, answers, qs.sector_questions)

  const set = (patch: Partial<PracticeProfile>) => { setP((cur) => ({ ...cur, ...patch })); setPicked(null) }
  const setC = (patch: Record<string, unknown>) => set({ constraints: { ...cons, ...patch } })
  const setA = (id: string, v: string) => { setAnswers((a) => ({ ...a, [id]: v })); setPicked(null) }
  const setOffice = (i: number, patch: Partial<Office>) => set({ existing_offices: offices.map((o, j) => (j === i ? { ...o, ...patch } : o)) })
  const num = (v: string) => (v === '' ? null : Number(v))
  const setKind = (v: string) => {
    const low = v.trim().toLowerCase()
    const medical = SPECIALTIES.includes(low) || /dentist|clinic|medical|therap|dialysis|orthop|physician|doctor|urgent care/.test(low)
    if (medical) set({ specialty: low.replace(/ /g, '_'), business_type: '', stock_pack: null })
    else set({ specialty: '', business_type: v, payers_accepted: [], stock_pack: null })
  }

  const use = (preset: Preset) => {
    const parsed = parseAnswers(preset.profile.free_text)
    setP(preset.profile); setAnswers(parsed.answers); setNotes(parsed.notes); setPicked(preset.id)
  }
  const profile = (): PracticeProfile => ({
    ...p,
    services: (p.services ?? []).map((s) => s.trim()).filter(Boolean),
    existing_offices: offices.filter((o) => o.address?.trim() || o.lat != null).map((o, i) => ({ ...o, name: o.name.trim() || `Location ${i + 1}` })),
    free_text: composeFreeText(answers, notes, all),
  })
  const submit = () => { setTried(true); if (missing.length === 0) onRun(profile()) }

  /** One question, rendered by kind; answers land in the contract field the question names. */
  const field = (q: IntakeQuestion) => {
    const value = q.field === 'customers' ? p.customers ?? '' : q.field === 'services' ? (p.services ?? []).join(', ') : answers[q.id] ?? ''
    const write = (v: string) => (q.field === 'customers' ? set({ customers: v })
      : q.field === 'services' ? set({ services: v.split(',').map((s) => s.trimStart()) }) : setA(q.id, v))
    const head = <span>{q.text}{q.required && <em className="req"> required</em>}</span>
    if (q.kind === 'choice' || q.kind === 'multi') {
      const chosen = q.field === 'payers' ? (p.payers_accepted ?? []).map((x) => PAYER_LABELS[x] ?? x)
        : q.kind === 'choice' ? (value ? [value] : [])
        // options may contain commas ("Helps, not essential"): match whole options, keep anything else typed
        : [...q.options.filter((o) => `, ${value}, `.includes(`, ${o}, `)),
           ...q.options.reduce((rest, o) => rest.split(o).join(''), value).split(',').map((x) => x.trim()).filter(Boolean)]
      const toggle = (o: string) => {
        const next = q.kind === 'choice' ? (chosen.includes(o) ? [] : [o]) : chosen.includes(o) ? chosen.filter((x) => x !== o) : [...chosen, o]
        if (q.field === 'payers') set({ payers_accepted: next.map((x) => PAYER_IDS[x]).filter(Boolean) })
        else write(next.join(', '))
      }
      return (
        <div className="fl q" key={q.id}>{head}
          <div className="pills">{q.options.map((o) => (
            <button type="button" key={o} className={`pill ${chosen.includes(o) ? 'on' : ''}`} aria-pressed={chosen.includes(o)} onClick={() => toggle(o)}>{o}</button>))}</div>
          <small className="why">{q.why}</small>
        </div>
      )
    }
    return (
      <label className="fl q" key={q.id}>{head}
        {q.kind === 'long'
          ? <textarea rows={2} value={value} placeholder={q.placeholder} onChange={(e) => write(e.target.value)} />
          : <input value={value} placeholder={q.placeholder} onChange={(e) => write(e.target.value)} />}
        <small className="why">{q.why}</small>
      </label>
    )
  }
  const pullHints = (
    <div className="hints" key="pull-hints">{AREA_HINTS.map((h) => (
      <button type="button" key={h} onClick={() => setA('pull', answers.pull?.trim() ? `${answers.pull.trim().replace(/[.]?$/, '.')} ${h}.` : `${h}.`)}>+ {h}</button>))}</div>
  )
  const inStep = (step: string) => qs.base.filter((q) => STEP_OF[q.id] === step && q.kind !== 'locations')
    .flatMap((q) => (q.id === 'pull' ? [field(q), pullHints] : [field(q)]))
  const sectorQs = qs.sector_questions

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
        <span className="tag" style={{ left: 'calc(6% + 10px)', bottom: 10 }}>Census · CMS · CDC · USDA · City of Houston</span>
        <span className="tag" style={{ right: 'calc(6% + 10px)', bottom: 10 }}>CoStar · Vexcel · Matterport</span>
        <span className="dot" style={{ left: '6%', top: 0 }} />
        <span className="dot" style={{ right: '6%', top: 0, transform: 'translate(50%,-50%)' }} />
        <span className="dot" style={{ left: '6%', top: '100%' }} />
        <span className="dot" style={{ right: '6%', top: '100%', transform: 'translate(50%,-50%)' }} />

        <div className="composer">
          <div>
            {live && (
              <div className="tabs intake-mode" role="tablist">
                <button role="tab" aria-selected={mode === 'form'} className={mode === 'form' ? 'on' : ''} onClick={() => setMode('form')}>Guided questions</button>
                <button role="tab" aria-selected={mode === 'chat'} className={mode === 'chat' ? 'on' : ''} onClick={() => setMode('chat')}>Talk it through</button>
              </div>
            )}
            {mode === 'chat' && live
              ? <Chat onUse={(draft) => { const parsed = parseAnswers(draft.free_text); setP(draft); setAnswers(parsed.answers); setNotes(parsed.notes); setPicked(null); setMode('form') }} />
              : (
          <form className="form" onSubmit={(e) => { e.preventDefault(); submit() }}>
            <section className="step">
              <span className="sn">1/6</span>
              <div className="sb">
                <h3>Your business</h3>
                {live && (
                  <div className="fl siteread"><span>Start from your website</span>
                    <div className="siterow">
                      <input type="url" value={site} placeholder="https://yourbusiness.com" onChange={(e) => setSite(e.target.value)}
                        onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); readSite() } }} />
                      <button type="button" className="pillbtn dark" disabled={reading || !site.trim()} onClick={readSite}>{reading ? 'Reading…' : 'Fill from website'}</button>
                    </div>
                    <small className="why">{siteMsg ?? 'The agent reads your site (locations, services, insurance, who you serve) and fills in what it states. You check every answer.'}</small>
                  </div>
                )}
                <div className="grid2">
                  <label className="fl"><span>Name</span>
                    <input value={p.name} required onChange={(e) => set({ name: e.target.value })} placeholder="Eastside Community Pantry" /></label>
                  <label className="fl"><span>What kind of business is it?</span>
                    <input value={kind} required list="kinds" placeholder="food pantry, laundromat, pediatric dentist…" onChange={(e) => setKind(e.target.value)} />
                    <datalist id="kinds">{[...SPECIALTIES, ...KIND_HINTS].map((k) => <option key={k} value={k} />)}</datalist></label>
                </div>
                {live && kind.trim() && (
                  <p className="sector" aria-live="polite"><b>{qs.label}</b> questions loaded{healthcare && !PACKED.has(p.specialty ?? '') ? ' · general healthcare data (no specialty pack yet)' : ''}</p>
                )}
                {!qs.sector_questions.some((q) => q.field === 'services') && (
                  <label className="fl"><span>Services</span>
                    <input value={(p.services ?? []).join(', ')} placeholder="wash and fold, self-service"
                      onChange={(e) => set({ services: e.target.value.split(',').map((s) => s.trimStart()) })} /></label>
                )}
              </div>
            </section>

            <section className="step">
              <span className="sn">2/6</span>
              <div className="sb">
                <h3>Where you are today</h3>
                <p className="lead">The most useful thing you can give the agent. Your own sites show what a good neighborhood looks like for you, and their customers are subtracted so a new site isn’t just stealing from them.</p>
                <div className="fl"><span>Current locations<em className="req"> required</em></span>
                  {offices.map((o, i) => (
                    <div className="orow" key={i}>
                      <input value={o.name} placeholder="Name" onChange={(e) => setOffice(i, { name: e.target.value })} />
                      <input value={o.address ?? ''} placeholder={o.lat != null ? 'Pinned on map' : 'Street address'}
                        onChange={(e) => setOffice(i, { address: e.target.value, lat: null, lon: null })} />
                      <input type="number" min={1} value={o.clinicians ?? 1} title={healthcare ? 'Clinicians' : 'Staff'} onChange={(e) => setOffice(i, { clinicians: Number(e.target.value) })} />
                      <button type="button" className="x" aria-label="Remove location" onClick={() => set({ existing_offices: offices.filter((_, j) => j !== i) })}>×</button>
                    </div>
                  ))}
                  <div className="locfoot">
                    <button type="button" className="addl" onClick={() => { set({ existing_offices: [...offices, { name: '', address: '', clinicians: 2 }] }); if (first) setA('locations', '') }}>+ Add a location</button>
                    <label className="check"><input type="checkbox" checked={first} disabled={offices.some((o) => o.address?.trim())}
                      onChange={(e) => setA('locations', e.target.checked ? FIRST_LOCATION : '')} /> This is our first location</label>
                  </div>
                </div>
                {inStep('locations')}
              </div>
            </section>

            <section className="step">
              <span className="sn">3/6</span>
              <div className="sb">
                <h3>Your {who}</h3>
                {inStep('customers')}
                <div className="fl"><span>Languages your staff speak</span>
                  <div className="pills">{[...LANGS, ...(p.languages ?? []).filter((l) => !LANGS.includes(l))].map((l) => (
                    <button type="button" key={l} className={`pill ${(p.languages ?? []).includes(l) ? 'on' : ''}`} aria-pressed={(p.languages ?? []).includes(l)}
                      onClick={() => set({ languages: (p.languages ?? []).includes(l) ? (p.languages ?? []).filter((x) => x !== l) : [...(p.languages ?? []), l] })}>{l}</button>))}</div>
                </div>
              </div>
            </section>

            {sectorQs.length > 0 && (
              <section className="step sector-step">
                <span className="sn">4/6</span>
                <div className="sb">
                  <h3>For a {qs.label.toLowerCase()}</h3>
                  <p className="lead">Picked from what you typed. These are the details that separate a site that works for this kind of business from one that doesn’t.</p>
                  {sectorQs.map(field)}
                </div>
              </section>
            )}

            <section className="step feature">
              <span className="sn">5/6</span>
              <div className="sb">
                <h3>What makes you different</h3>
                <p className="lead">This turns a generic search into yours. The agent turns each detail into a factor, then goes looking for public data to measure it in every Houston neighborhood.</p>
                {inStep('edge')}
                <label className="fl"><span>Anything else?</span>
                  <textarea rows={2} value={notes} onChange={(e) => { setNotes(e.target.value); setPicked(null) }} placeholder="Grant service areas, a site you already tried, a landlord you trust…" /></label>
              </div>
            </section>

            <section className="step">
              <span className="sn">6/6</span>
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
                {inStep('space')}
              </div>
            </section>

            <div className="form-foot">
              {picked && hasRecording(picked)
                ? <button type="button" className="linkbtn" onClick={() => onRun(profile(), picked)}>Play the recorded run instead</button>
                : <span className={`foot-note ${tried && missing.length ? 'warn' : ''}`}>
                    {missing.length && live ? `Still needed: ${missing.join(', ')}.` : live ? 'Runs the live agent.' : 'Replays a recorded run.'}</span>}
              <button className="cta big" type="submit" disabled={live && missing.length > 0}><span className="arrow">↗</span>
                <span className="label">{live ? 'Find sites' : 'Replay a recorded run'}</span></button>
            </div>
          </form>
              )}
          </div>

          <div className="templates">
            <div className="tpl-label">Or start from</div>
            {PRESETS.map((preset, i) => (
              <button key={preset.id} className={`tpl ${picked === preset.id ? 'on' : ''}`} style={{ background: TILE[i % TILE.length].bg }} onClick={() => { use(preset); setMode('form') }}>
                <span className="shape" style={TILE[i % TILE.length].shape} /><b>{preset.label}</b>
              </button>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

const GREETING = 'Tell me what you want to open in Houston, who your customers are, and where your current locations are (or that this is your first).'

/** Talk it through: the agent asks the base and sector questions one or two at a time and fills the same form. */
function Chat({ onUse }: { onUse: (draft: PracticeProfile) => void }) {
  const [messages, setMessages] = useState<ChatMessage[]>([{ role: 'assistant', content: GREETING }])
  const [draft, setDraft] = useState<PracticeProfile | null>(null)
  const [missing, setMissing] = useState<string[]>([])
  const [ready, setReady] = useState(false)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const thread = useRef<HTMLOListElement>(null)
  useEffect(() => { thread.current?.scrollTo({ top: thread.current.scrollHeight, behavior: 'smooth' }) }, [messages, busy])

  const send = async () => {
    const words = text.trim()
    if (!words || busy) return
    const next: ChatMessage[] = [...messages, { role: 'user', content: words }]
    setMessages(next); setText(''); setBusy(true)
    try {
      const res = await intakeChat(next.slice(1), draft)
      setDraft(res.profile); setMissing(res.missing ?? []); setReady(Boolean(res.ready))
      setMessages([...next, { role: 'assistant', content: res.reply }])
    } catch {
      setMessages([...next, { role: 'assistant', content: 'I can’t reach the agent right now. Switch to Guided questions to fill in the form.' }])
    } finally { setBusy(false) }
  }
  const got = draft ? parseAnswers(draft.free_text).answers : {}

  return (
    <div className="chat">
      <ol className="thread" ref={thread} aria-live="polite">
        {messages.map((m, i) => <li key={i} className={`bubble ${m.role}`}>{m.content}</li>)}
        {busy && <li className="bubble assistant typing">Reading that…</li>}
      </ol>
      <form className="chatbox" onSubmit={(e) => { e.preventDefault(); send() }}>
        <textarea rows={2} value={text} aria-label="Describe your business" placeholder="We run a choice pantry in Gulfton for families and seniors; most clients come by bus…"
          onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }} />
        <button className="pillbtn dark" type="submit" disabled={busy || !text.trim()}>Send</button>
      </form>
      {draft && (
        <div className="understood">
          <div className="uhead"><b>What I have so far</b>
            <span className={`chip ${ready ? 'ready' : ''}`}>{ready ? 'Enough to search' : `Still need: ${missing.join(', ')}`}</span></div>
          <dl>
            <dt>Business</dt><dd>{kindOf(draft) || '—'}</dd>
            <dt>Customers</dt><dd>{draft.customers || '—'}</dd>
            <dt>Locations</dt><dd>{(draft.existing_offices ?? []).map((o) => o.address).join(' · ') || (got.locations ? 'First location' : '—')}</dd>
            <dt>Answers</dt><dd>{Object.keys(got).length} of the intake questions</dd>
          </dl>
          <button type="button" className="pillbtn" onClick={() => onUse(draft)}>Review in the form{ready ? ' and find sites' : ''}</button>
        </div>
      )}
    </div>
  )
}
