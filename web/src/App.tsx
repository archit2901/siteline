import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react'
import { AGENT_URL, approve, createRun, listGolden, loadRunArtifact, rescore } from './api/client'
import { FIXTURE_BASE, loadFixtureEvents, startReplay, type Replay } from './api/replay'
import { subscribeToRun, type Subscription } from './api/sse'
import ApprovalPanel from './components/ApprovalModal'
import FactorList from './components/FactorList'
import Icon, { Mark } from './components/Icon'
import IntakeForm, { PRESETS, type Preset, type Source } from './components/IntakeForm'
import MapView, { type GridCell } from './components/MapView'
import { BusinessProfile, ReportNotes } from './components/Profile'
import RunLine, { BRANCH_COLORS } from './components/RunLine'
import { ResultsTable, SiteFlyer } from './components/SiteCards'
import { initialRunState, runReducer, type RunStatus } from './state/runReducer'
import type { ApproveRequest, PracticeProfile, ScoringSpec } from './types/contracts'

const DEFAULT_SOURCE: Source = (import.meta.env.VITE_FIXTURE_MODE ?? '1') === '1' ? 'offline' : 'live'
const LANGSMITH_URL = import.meta.env.VITE_LANGSMITH_URL as string | undefined

const STATUS_TEXT: Record<RunStatus, string> = {
  idle: 'Ready',
  running: 'Working',
  waiting: 'Waiting for your approval',
  done: 'Done',
  error: 'Stopped',
}
const SOURCE_TEXT: Record<Source, string> = { live: 'Live agent', recorded: 'Recorded run', offline: 'Offline demo' }

type Tab = 'site' | 'factors' | 'sources'

export default function App() {
  const [state, dispatch] = useReducer(runReducer, initialRunState)
  const [grid, setGrid] = useState<GridCell[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)
  const [preset, setPreset] = useState<Preset>(PRESETS[0])
  const [profile, setProfile] = useState<PracticeProfile>(PRESETS[0].profile)
  const [selectedSite, setSelectedSite] = useState<string | null>(null)
  const [source, setSource] = useState<Source>(DEFAULT_SOURCE)
  const [golden, setGolden] = useState<string[]>([])
  const [tab, setTab] = useState<Tab>('site')
  const [userPicked, setUserPicked] = useState(false)
  const [spec, setSpec] = useState<ScoringSpec | null>(null)
  const [tuning, setTuning] = useState(false)
  const replay = useRef<Replay | null>(null)
  const live = useRef<Subscription | null>(null)

  useEffect(() => {
    fetch(`${FIXTURE_BASE}/grid.json`)
      .then((r) => {
        if (!r.ok) throw new Error(`Could not load the Houston grid (HTTP ${r.status}). Run "npm run fixtures".`)
        return r.json() as Promise<GridCell[]>
      })
      .then(setGrid)
      .catch((e: Error) => setLoadError(e.message))
    listGolden().then(setGolden)
    return () => {
      replay.current?.stop()
      live.current?.close()
    }
  }, [])

  const recordedAvailable = golden.includes(preset.id)

  const choosePreset = useCallback(
    (p: Preset) => {
      setPreset(p)
      setProfile(p.profile)
      if (source === 'recorded' && !golden.includes(p.id)) setSource('live')
    },
    [source, golden],
  )

  const start = useCallback(async () => {
    replay.current?.stop()
    live.current?.close()
    setSelectedSite(null)
    setUserPicked(false)
    setLoadError(null)
    setTab('site')
    try {
      if (source === 'offline') {
        const events = await loadFixtureEvents()
        replay.current = startReplay(events, dispatch, { runId: 'offline-demo' })
        return
      }
      const { run_id: runId } = await createRun(profile, source === 'recorded' ? preset.id : undefined)
      dispatch({ type: 'start', runId })
      live.current = subscribeToRun(
        runId,
        AGENT_URL,
        (event) => {
          dispatch({ type: 'event', event })
          if (event.type === 'file') {
            loadRunArtifact(runId, String(event.payload?.name ?? ''))
              .then((action) => action && dispatch(action))
              .catch((e: Error) => setLoadError(e.message))
          }
        },
        setLoadError,
      )
    } catch (e) {
      setLoadError(source === 'offline' ? (e as Error).message : `Could not reach the agent at ${AGENT_URL}. Is agent-api running? (${(e as Error).message})`)
    }
  }, [source, profile, preset.id])

  const answerApproval = useCallback(
    async (answer: ApproveRequest) => {
      const approval = state.approval
      if (!approval) return
      if (approval.kind === 'weights' && approval.spec) setSpec(answer.edited_spec ?? approval.spec)
      if (source === 'offline') {
        replay.current?.answer(approval.approval_id)
        return
      }
      try {
        await approve(state.runId!, answer)
        dispatch({ type: 'approvalAnswered', approvalId: approval.approval_id })
      } catch (e) {
        setLoadError((e as Error).message)
      }
    },
    [source, state.approval, state.runId],
  )

  // Tune weights after a live run: /rescore re-ranks the same spaces with no model call.
  const retune = useCallback(
    async (weights: Record<string, number>) => {
      if (!spec || !state.runId) return
      const next = { ...spec, weights }
      setSpec(next)
      setTuning(true)
      try {
        dispatch({ type: 'rescored', data: await rescore(state.runId, next) })
      } catch (e) {
        setLoadError((e as Error).message)
      } finally {
        setTuning(false)
      }
    },
    [spec, state.runId],
  )

  const reset = useCallback(() => {
    replay.current?.stop()
    replay.current = null
    live.current?.close()
    live.current = null
    setSelectedSite(null)
    setUserPicked(false)
    setSpec(null)
    setLoadError(null)
    dispatch({ type: 'reset' })
  }, [])

  const factors = useMemo(() => state.factorOrder.map((id) => state.factors[id]), [state.factorOrder, state.factors])
  const featureLabels = useMemo(() => {
    const labels: Record<string, string> = {}
    for (const f of [...(state.report?.factors ?? []), ...factors]) if (f.feature_id) labels[f.feature_id] = f.factor
    return labels
  }, [factors, state.report])
  const scouts = useMemo(() => state.scoutOrder.map((id) => state.scouts[id]), [state.scoutOrder, state.scouts])
  const report = state.report
  const sites = report?.recommendations ?? []

  // Open the top-ranked space's flyer when the report lands.
  useEffect(() => {
    if (report?.recommendations.length) setSelectedSite((cur) => cur ?? report.recommendations[0].site.id)
  }, [report])

  const selectedRec = sites.find((r) => r.site.id === selectedSite)
  const selectSite = useCallback((id: string) => {
    setSelectedSite(id)
    setUserPicked(true)
    setTab('site')
  }, [])
  const scoutColors = useMemo(
    () => Object.fromEntries(state.scoutOrder.map((id, k) => [id, BRANCH_COLORS[k % BRANCH_COLORS.length]])),
    [state.scoutOrder],
  )

  const idle = state.status === 'idle'
  const businessName = report?.profile.name ?? (idle ? null : source === 'offline' ? 'Offline demo' : profile.name)
  const businessKind = report?.profile.business_type || report?.profile.specialty?.replace('_', ' ') || (idle ? '' : profile.business_type || profile.specialty?.replace('_', ' '))
  const elapsed = state.startedAt && state.lastAt ? Math.max(0, (Date.parse(state.lastAt) - Date.parse(state.startedAt)) / 1000) : null
  const u = state.usage

  return (
    <div className="app">
      <header className="bar">
        <div className="wordmark">
          <Mark />
          Siteline
        </div>
        {businessName && (
          <div className="business">
            <b>{businessName}</b>
            <span>{[businessKind, report?.demand_label && report.demand_label !== 'patients' ? `counting ${report.demand_label}` : null].filter(Boolean).join(', ')}</span>
          </div>
        )}
        <span className="spacer" />
        {u && (
          <div className="meter" aria-label="Model usage for this run">
            <div>
              <b>{u.calls}</b>model calls
            </div>
            <div>
              <b>{u.cache_hits}</b>cached
            </div>
            <div className="wide">
              <b>{(u.input_tokens + u.output_tokens).toLocaleString('en-US')}</b>tokens
            </div>
            <div>
              <b>${u.cost_usd.toFixed(3)}</b>cost
            </div>
            {elapsed != null && (
              <div className="wide">
                <b>{Math.round(elapsed)} s</b>time
              </div>
            )}
          </div>
        )}
        {idle ? (
          <div className="segmented" role="group" aria-label="Run source">
            {(['live', 'recorded', 'offline'] as Source[]).map((s) => (
              <button
                key={s}
                aria-pressed={source === s}
                disabled={s === 'recorded' && !recordedAvailable}
                title={s === 'recorded' && !recordedAvailable ? 'No recorded run of this example on the server' : undefined}
                onClick={() => setSource(s)}
              >
                {SOURCE_TEXT[s]}
              </button>
            ))}
          </div>
        ) : (
          <span className="chip">{SOURCE_TEXT[source]}</span>
        )}
        <span className="status" data-state={state.status} role="status">
          <span className="dot" />
          {STATUS_TEXT[state.status]}
        </span>
        {state.status === 'done' && source !== 'offline' && LANGSMITH_URL && (
          <a className="button" href={LANGSMITH_URL} target="_blank" rel="noreferrer" title="Open this project's runs in LangSmith">
            <Icon name="trace" />
            Trace
          </a>
        )}
        {!idle && (
          <button className="button" onClick={reset}>
            <Icon name="back" />
            New search
          </button>
        )}
      </header>

      <main className={`main${report ? ' done' : ''}`}>
        <aside className="panel left" aria-label={idle ? 'Describe the business' : 'The run'}>
          {idle ? (
            <IntakeForm
              profile={profile}
              presetId={preset.id}
              source={source}
              recordedIds={golden}
              onPreset={choosePreset}
              onChange={setProfile}
              onSubmit={start}
            />
          ) : (
            <RunLine
              todos={state.todos}
              scouts={scouts}
              factors={state.factors}
              trace={state.trace}
              status={state.status}
              profileSummary={state.profileSummary}
              result={report && state.status === 'done' ? { count: report.recommendations.length, verified: Boolean(report.verified) } : null}
            />
          )}
        </aside>

        <section className={`center${report ? ' with-results' : ''}`}>
          <div className="mapwrap">
            {(loadError || state.error) && <p className="banner">{loadError ?? state.error}</p>}
            <MapView
              grid={grid}
              zones={state.zones}
              sites={sites}
              featureLabels={featureLabels}
              selectedSiteId={selectedSite}
              onSelectSite={selectSite}
              demandLabel={report?.demand_label}
              trip={report?.demand?.trip ?? null}
              focusSelection={userPicked}
            />
            {state.approval && (
              <ApprovalPanel
                approval={state.approval}
                featureLabels={featureLabels}
                readOnly={source !== 'live'}
                recorded={source === 'recorded'}
                onApprove={answerApproval}
                onReject={() => state.approval && answerApproval({ approval_id: state.approval.approval_id, decision: 'reject' })}
              />
            )}
          </div>
          {report && (
            <ResultsTable
              report={report}
              selectedSiteId={selectedSite}
              onSelect={selectSite}
              tune={source === 'live' && spec ? { spec, featureLabels, busy: tuning, onChange: retune } : undefined}
            />
          )}
        </section>

        <aside className="panel right" aria-label="Details">
          {idle ? (
            <div className="tabpanel route-preview">
              <RunLine todos={[]} scouts={[]} factors={{}} trace={[]} status="idle" profileSummary={null} />
            </div>
          ) : report ? (
            <>
              <div className="tabs" role="tablist" aria-label="Details">
                {(
                  [
                    ['site', 'Space'],
                    ['factors', 'Factors'],
                    ['sources', 'Sources and caveats'],
                  ] as [Tab, string][]
                ).map(([id, label]) => (
                  <button key={id} role="tab" aria-selected={tab === id} onClick={() => setTab(id)}>
                    {label}
                    {id === 'factors' && <span className="count">{factors.length || report.factors.length}</span>}
                  </button>
                ))}
              </div>
              <div className="tabpanel" role="tabpanel">
                {tab === 'site' && <SiteFlyer report={report} rec={selectedRec} featureLabels={featureLabels} onSelect={selectSite} />}
                {tab === 'factors' && (
                  <>
                    <BusinessProfile report={report} summary={state.profileSummary} />
                    <FactorList factors={factors.length ? factors : report.factors} report={report} scoutColors={scoutColors} />
                  </>
                )}
                {tab === 'sources' && <ReportNotes report={report} featureLabels={featureLabels} />}
              </div>
            </>
          ) : (
            <div className="tabpanel">
              <BusinessProfile report={null} summary={state.profileSummary} />
              <FactorList factors={factors} report={null} scoutColors={scoutColors} />
            </div>
          )}
        </aside>
      </main>
    </div>
  )
}
