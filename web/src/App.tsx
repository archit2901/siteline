import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react'
import { AGENT_URL, approve, createRun, listGolden, loadRunArtifact, rescore, loadStrategy, loadReview, type Review, type Strategy } from './api/client'
import { FIXTURE_BASE, loadFixtureEvents, startReplay, type Replay } from './api/replay'
import { subscribeToRun, type Subscription } from './api/sse'
import { Approval, Factors, LogDrawer, Plan } from './components/Agent'
import MapView, { type GridCell } from './components/MapView'
import PropertySheet from './components/PropertySheet'
import Ask from './components/Ask'
import Space from './components/Space'
import Decision from './components/Decision'
import { Proof, SiteCard, Tune, ZoneCard, stableId, topZones } from './components/Results'
import Start, { PRESETS } from './components/Start'
import { initialRunState, runReducer, type RunStatus } from './state/runReducer'
import type { ApproveRequest, PracticeProfile, ScoringSpec } from './types/contracts'

const DEFAULT_MODE: Mode = (import.meta.env.VITE_FIXTURE_MODE ?? '1') === '1' ? 'fixture' : 'live'
type Mode = 'fixture' | 'live'
type View = 'start' | 'agent' | 'results'
type Tab = 'decision' | 'lease' | 'build' | 'space' | 'proof' | 'ask'

const HEADLINE: Record<RunStatus, string> = { idle: 'Ready.', running: 'Working…', waiting: 'Your call.', done: 'Done.', error: 'Stopped.' }

export default function App() {
  const [state, dispatch] = useReducer(runReducer, initialRunState)
  const [grid, setGrid] = useState<GridCell[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)
  const [profile, setProfile] = useState<PracticeProfile>(PRESETS[0].profile)
  const [mode, setMode] = useState<Mode>(DEFAULT_MODE)
  const [golden, setGolden] = useState<string[]>([])
  const [replaying, setReplaying] = useState(false)
  const [view, setView] = useState<View>('start')
  const [tab, setTab] = useState<Tab>('lease')
  const [selected, setSelected] = useState<string | null>(null)
  const [sheet, setSheet] = useState<string | null>(null)
  const [focus, setFocus] = useState<{ lat: number; lon: number; zoom?: number } | null>(null)
  const [log, setLog] = useState(false)
  const [spec, setSpec] = useState<ScoringSpec | null>(null) // the weights the run scored with (for re-tuning)
  const [tuning, setTuning] = useState(false)
  const [strategy, setStrategy] = useState<Strategy | null>(null)
  const [review, setReview] = useState<Review | null>(null)
  const pendingWeights = useRef<Record<string, number> | null>(null) // tuned weights carried into a re-run
  const replay = useRef<Replay | null>(null)
  const live = useRef<Subscription | null>(null)
  const fixtureMode = mode === 'fixture'

  useEffect(() => {
    fetch(`${FIXTURE_BASE}/grid.json`)
      .then((r) => {
        if (!r.ok) throw new Error(`Could not load the Houston grid (HTTP ${r.status}). Run "npm run fixtures".`)
        return r.json() as Promise<GridCell[]>
      })
      .then(setGrid)
      .catch((e: Error) => setLoadError(e.message))
    return () => { replay.current?.stop(); live.current?.close() }
  }, [])

  useEffect(() => { if (!fixtureMode) listGolden().then(setGolden) }, [fixtureMode])

  const start = useCallback(async (p: PracticeProfile, recorded?: string) => {
    replay.current?.stop()
    live.current?.close()
    setProfile(p); setSelected(null); setSheet(null); setFocus(null); setTab('lease'); setLoadError(null); setSpec(null); setStrategy(null); setReview(null)
    setReplaying(Boolean(recorded)); setView('agent')
    try {
      if (fixtureMode) {
        const events = await loadFixtureEvents()
        replay.current = startReplay(events, dispatch, { runId: 'fixture-replay' })
        return
      }
      const { run_id: runId } = await createRun(p, recorded)
      dispatch({ type: 'start', runId })
      live.current = subscribeToRun(runId, AGENT_URL, (event) => {
        dispatch({ type: 'event', event })
        if (event.type === 'file' && event.payload?.name === 'strategy.json') loadStrategy(runId).then(setStrategy).catch(() => setStrategy(null))
        if (event.type === 'file' && event.payload?.name === 'review.json') loadReview(runId).then(setReview).catch(() => setReview(null))
        if (event.type === 'file') {
          loadRunArtifact(runId, String(event.payload?.name ?? ''))
            .then((action) => action && dispatch(action))
            .catch((e: Error) => setLoadError(e.message))
        }
      }, setLoadError)
    } catch (e) {
      setLoadError((e as Error).message)
    }
  }, [fixtureMode])

  const answer = useCallback(async (decision: ApproveRequest) => {
    const approval = state.approval
    if (!approval) return
    if (approval.kind === 'weights' && approval.spec) { setSpec(decision.edited_spec ?? approval.spec); pendingWeights.current = null }
    if (fixtureMode) { replay.current?.answer(approval.approval_id); return }
    try {
      await approve(state.runId!, decision)
      dispatch({ type: 'approvalAnswered', approvalId: approval.approval_id })
    } catch (e) {
      setLoadError((e as Error).message)
    }
  }, [fixtureMode, state.approval, state.runId])

  const reset = useCallback(() => {
    replay.current?.stop(); replay.current = null
    live.current?.close(); live.current = null
    setSelected(null); setSheet(null); setLoadError(null); setView('start')
    dispatch({ type: 'reset' })
  }, [])

  useEffect(() => { if (state.status === 'done' && state.report) setView('results') }, [state.status, state.report])

  const factors = useMemo(() => state.factorOrder.map((id) => state.factors[id]), [state.factorOrder, state.factors])
  const featureLabels = useMemo(() => {
    const labels: Record<string, string> = {}
    for (const f of [...(state.report?.factors ?? []), ...factors]) if (f.feature_id) labels[f.feature_id] = f.factor
    return labels
  }, [factors, state.report])
  const report = state.report
  const sites = report?.recommendations ?? []
  const zones = useMemo(() => (state.zones ? topZones(state.zones, grid) : []), [state.zones, grid])
  const offices = report?.profile.existing_offices ?? profile.existing_offices ?? []
  const name = report?.profile.name ?? profile.name
  const recorded = fixtureMode || replaying
  const open = sites.find((r) => r.site.id === sheet) ?? null
  const step = `${Math.max(1, state.todos.findIndex((t) => t.status === 'in_progress') + 1)}/${state.todos.length || 1}`
  const canTune = !recorded && Boolean(spec) && Boolean(state.runId)

  const retune = useCallback(async (weights: Record<string, number>) => {
    if (!spec || !state.runId || !report) return
    const total = Object.values(weights).reduce((a, b) => a + b, 0) || 1
    const next: ScoringSpec = { ...spec, weights: Object.fromEntries(Object.entries(weights).map(([k, v]) => [k, v / total])) }
    setTuning(true)
    try {
      const res = await rescore(state.runId, next)
      dispatch({ type: 'artifact', name: 'zones.json', data: res.zones })
      dispatch({ type: 'artifact', name: 'report.json', data: { ...report, recommendations: res.recommendations } })
    } catch (e) {
      setLoadError((e as Error).message)
    } finally {
      setTuning(false)
    }
  }, [spec, state.runId, report])

  return (
    <>
      <nav className="nav">
        <button className="logo" onClick={reset} aria-label="Siteline home"><i aria-hidden="true" />SITELINE</button>
        <div className="steps">
          <button className={view === 'start' ? 'on' : ''} onClick={() => setView('start')}>Describe</button>
          <button className={view === 'agent' ? 'on' : ''} disabled={state.status === 'idle'} onClick={() => setView('agent')}>Agent</button>
          <button className={view === 'results' ? 'on' : ''} disabled={!report} onClick={() => setView('results')}>Results</button>
        </div>
        <div className="nav-r">
          {state.status === 'idle'
            ? <div className="mode" role="radiogroup" aria-label="Data source">
                <button className={fixtureMode ? 'on' : ''} onClick={() => setMode('fixture')}>Recorded</button>
                <button className={!fixtureMode ? 'on' : ''} onClick={() => setMode('live')}>Live agent</button>
              </div>
            : <span className="chip-mode">{recorded ? 'Recorded run' : 'Live agent'}</span>}
          <button className="cta" onClick={reset}><span className="arrow">↗</span><span className="label">New search</span></button>
        </div>
      </nav>

      {view === 'start' && <Start live={!fixtureMode} hasRecording={(id) => !fixtureMode && golden.includes(id)} onRun={start} />}

      {view !== 'start' && (
        <main className={`work ${view === 'results' ? 'results' : ''}`}>
          {view === 'agent' && (
            <aside className="panel">
              <span className="hand">{recorded && fixtureMode ? 'Recorded run' : name}</span>
              <h2 className="h2">{HEADLINE[state.status]}</h2>
              <p className="sub">{recorded ? 'Replaying a recorded run' : 'Live agent'}</p>
              <Plan todos={state.todos} />
              <Factors factors={factors} scouts={state.scouts} />
              {(loadError || state.error) && <div className="err">{loadError ?? state.error}</div>}
            </aside>
          )}

          <section className="mapcard">
            <MapView grid={grid} zones={state.zones} sites={view === 'results' && tab !== 'build' ? sites : []}
              highlight={view === 'results' && tab === 'build' ? zones.map((z) => z.h3) : []} offices={offices}
              featureLabels={featureLabels} selectedSiteId={selected} onSelectSite={(id) => { setSelected(id); setSheet(id) }} focus={focus} />
            <div className="logbtn">
              <button className="pillbtn dark" onClick={() => setLog(true)} disabled={state.trace.length === 0}>Agent log · {state.trace.length}</button>
            </div>
            {state.approval && view === 'agent' && (
              <Approval key={state.approval.approval_id} approval={state.approval} featureLabels={featureLabels} readOnly={fixtureMode}
                preset={state.approval.kind === 'weights' ? pendingWeights.current : null}
                step={step} businessName={name} onApprove={answer}
                onReject={() => answer({ approval_id: state.approval!.approval_id, decision: 'reject' })} />
            )}
          </section>

          {view === 'results' && report && (
            <aside className="panel">
              <span className="hand">{report.profile.name}</span>
              <h2 className="h2">{tab === 'lease' ? 'Ten spaces to lease.' : tab === 'build' ? `${zones.length} places to build.` : tab === 'ask' ? 'Ask the results.' : tab === 'space' ? 'Lease, buy or build.' : tab === 'decision' ? 'The decision.' : 'How we know.'}</h2>
              <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
                <div className="tabs" role="tablist">
                  {review && <button role="tab" aria-selected={tab === 'decision'} className={tab === 'decision' ? 'on' : ''} onClick={() => setTab('decision')}>Decision</button>}
                  <button role="tab" aria-selected={tab === 'lease'} className={tab === 'lease' ? 'on' : ''} onClick={() => setTab('lease')}>Lease<sup>{sites.length}</sup></button>
                  <button role="tab" aria-selected={tab === 'build'} className={tab === 'build' ? 'on' : ''} onClick={() => setTab('build')}>Build<sup>{zones.length}</sup></button>
                  {strategy && <button role="tab" aria-selected={tab === 'space'} className={tab === 'space' ? 'on' : ''} onClick={() => setTab('space')}>Space</button>}
                  <button role="tab" aria-selected={tab === 'proof'} className={tab === 'proof' ? 'on' : ''} onClick={() => setTab('proof')}>Proof</button>
                  {!fixtureMode && <button role="tab" aria-selected={tab === 'ask'} className={tab === 'ask' ? 'on' : ''} onClick={() => setTab('ask')}>Ask</button>}
                </div>
              </div>
              <div className="verified">
                <span><b>{report.verified ? 'Verified' : 'Unverified'}</b> numbers</span>·
                <span><b>{report.factors.filter((f) => f.status === 'found').length}</b> datasets found online</span>·
                <span><b>{Object.keys(report.sources ?? {}).length}</b> cited sources</span>
              </div>
              {canTune && (tab === 'lease' || tab === 'build') && spec && <Tune spec={spec} featureLabels={featureLabels} busy={tuning} onRerank={retune}
                onRerun={(w) => { pendingWeights.current = Object.fromEntries(Object.entries(w).map(([k, v]) => [stableId(k), v])); start(report.profile) }} />}
              {tab === 'lease' && (
                <div className={`cards ${tuning ? 'dim' : ''}`}>{sites.map((r) => (
                  <SiteCard key={r.site.id} r={r} report={report} featureLabels={featureLabels} active={selected === r.site.id} onHover={setSelected}
                    onOpen={() => { setSheet(r.site.id); setFocus({ lat: r.site.lat, lon: r.site.lon }) }} />))}</div>
              )}
              {tab === 'build' && (
                <>
                  <p className="sub" style={{ marginTop: 14 }}>No building yet? These are the run’s highest-scoring zones, spread across the region.</p>
                  <div className="cards">{zones.map((z) => (
                    <ZoneCard key={z.h3} z={z} total={zones.length} featureLabels={featureLabels} onFocus={() => setFocus({ lat: z.lat, lon: z.lon, zoom: 12.5 })} />))}</div>
                </>
              )}
              {tab === 'proof' && <Proof report={report} spec={spec} />}
              {tab === 'decision' && review && <Decision r={review} onOpen={(id) => { setSelected(id); setSheet(id) }} />}
              {tab === 'space' && strategy && <Space s={strategy} onOpen={(id) => { setSelected(id); setSheet(id) }} />}
              {tab === 'ask' && <Ask report={report} onSelect={(id) => { setSelected(id); setSheet(id) }} />}
            </aside>
          )}
        </main>
      )}

      {log && <LogDrawer trace={state.trace} onClose={() => setLog(false)} />}
      {open && report && <PropertySheet rec={open} report={report} featureLabels={featureLabels} onClose={() => setSheet(null)} />}
    </>
  )
}
