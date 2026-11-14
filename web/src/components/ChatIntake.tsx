import { useEffect, useRef, useState } from 'react'
import { intakeChat } from '../api/client'
import type { ChatMessage, PracticeProfile } from '../types/contracts'
import Icon from './Icon'
import IntakeForm, { PRESETS, type Preset } from './IntakeForm'

const GREETING = 'Tell me what you want to open in Houston and who your customers are.'

const PAYER_WORDS: Record<string, string> = { medicare: 'Medicare', medicaid: 'Medicaid', chip: 'CHIP', commercial: 'commercial insurance', workers_comp: "workers' comp" }
const article = (w: string) => (/^[aeiou]/i.test(w) ? 'An' : 'A')

/** A preset as the sentence an owner would type. */
export function presetSentence(p: Preset): string {
  const what = (p.healthcare ? p.label : p.profile.business_type || p.label).toLowerCase()
  const who = p.profile.customers ? ` for ${p.profile.customers.replace(/\.$/, '').toLowerCase()}` : ''
  const payers = (p.profile.payers_accepted ?? []).map((x) => PAYER_WORDS[x] ?? x)
  const takes = !who && payers.length ? ` accepting ${payers.slice(0, -1).join(', ')}${payers.length > 1 ? ' and ' : ''}${payers.at(-1)}` : ''
  return `${article(what)} ${what}${who}${takes}`
}

interface Props {
  onSubmit: (profile: PracticeProfile) => void
}

/** Start screen in live mode: describe the business in your own words; the agent asks only for what is missing. */
export default function ChatIntake({ onSubmit }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([{ role: 'assistant', content: GREETING }])
  const [draft, setDraft] = useState<PracticeProfile | null>(null)
  const [ready, setReady] = useState(false)
  const [missing, setMissing] = useState<string[]>([])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [editing, setEditing] = useState(false)
  const thread = useRef<HTMLOListElement>(null)

  useEffect(() => {
    thread.current?.scrollTo({ top: thread.current.scrollHeight, behavior: 'smooth' })
  }, [messages, busy])

  const send = async (content: string, base: PracticeProfile | null = draft) => {
    const words = content.trim()
    if (!words || busy) return
    const next: ChatMessage[] = [...messages, { role: 'user', content: words }]
    setMessages(next)
    setText('')
    setBusy(true)
    try {
      const res = await intakeChat(next.filter((m, i) => i > 0 || m.role === 'user'), base)
      setDraft(res.profile)
      setReady(Boolean(res.ready))
      setMissing(res.missing ?? [])
      setMessages([...next, { role: 'assistant', content: res.reply }])
    } catch {
      setMessages([...next, { role: 'assistant', content: 'I can’t reach the agent right now. Open Edit details to fill in the form instead.' }])
      setDraft(base ?? { ...PRESETS[0].profile, name: 'My business', business_type: '', customers: '', existing_offices: [] })
      setEditing(true)
    } finally {
      setBusy(false)
    }
  }

  const started = messages.some((m) => m.role === 'user')
  const what = draft ? draft.business_type || draft.specialty?.replace('_', ' ') : ''
  const cons = draft?.constraints

  return (
    <div className="chat-intake">
      <section className="section">
        <h1>Where in Houston should your business open next?</h1>
        <p className="lede">Describe any business in your own words. The agent asks only for what it needs, then finds the data and ranks real spaces for lease.</p>
      </section>

      <ol className="thread" ref={thread} aria-live="polite">
        {messages.map((m, i) => (
          <li key={i} className={`bubble ${m.role}`}>
            {m.content}
          </li>
        ))}
        {busy && <li className="bubble assistant typing">Reading that...</li>}
      </ol>

      {!started && (
        <div className="suggest" role="group" aria-label="Examples (each sends a full description)">
          <span className="hint">Or start from an example:</span>
          {PRESETS.map((p) => (
            <button key={p.id} className="preset" title={presetSentence(p)} onClick={() => send(presetSentence(p), p.profile)}>
              <span className="ring" aria-hidden="true" />
              {p.label}
            </button>
          ))}
        </div>
      )}

      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault()
          send(text)
        }}
      >
        <textarea
          rows={2}
          value={text}
          placeholder={started ? 'Add details or answer the question' : 'A bakery for young families near good schools, about 1,500 sq ft'}
          aria-label="Describe your business"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              send(text)
            }
          }}
        />
        <button className="button primary" type="submit" disabled={busy || !text.trim()}>
          Send
        </button>
      </form>

      {draft && (
        <section className="section understood">
          <div className="understood-head">
            <h2>What I understood</h2>
            <span className={`chip${ready ? ' ready' : ''}`}>{ready ? 'Ready to search' : `Still need ${missing.join(' and ')}`}</span>
          </div>
          <dl>
            <dt>Business</dt>
            <dd>{what || 'Not yet'}</dd>
            <dt>Customers</dt>
            <dd>{draft.customers || 'Not yet'}</dd>
            <dt>Space</dt>
            <dd>
              {cons ? `${cons.min_sqft?.toLocaleString('en-US')} to ${cons.max_sqft?.toLocaleString('en-US')} sq ft` : 'Default size'}
              {cons?.max_rent_psf ? `, up to $${cons.max_rent_psf}/sq ft` : ''}
              {cons?.ground_floor ? ', ground floor' : ''}
            </dd>
            {draft.free_text && (
              <>
                <dt>Notes</dt>
                <dd>{draft.free_text}</dd>
              </>
            )}
          </dl>
          <button className="button" type="button" aria-expanded={editing} onClick={() => setEditing((e) => !e)}>
            {editing ? 'Hide details' : 'Edit details'}
          </button>
        </section>
      )}

      {draft && editing && <IntakeForm embedded profile={draft} presetId={null} source="live" recordedIds={[]} onPreset={() => {}} onChange={setDraft} onSubmit={() => onSubmit(draft)} />}

      <div className="go">
        <button className="button primary big block" disabled={!what || busy} onClick={() => draft && onSubmit(draft)}>
          <Icon name="play" />
          Find sites
        </button>
        <p className="hint">{what ? 'Runs the agent live on Amazon Nova. You approve the data plan and the weights.' : 'Describe the business first.'}</p>
      </div>
    </div>
  )
}
