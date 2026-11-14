import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react'
import { AGENT_URL, approve, createRun, loadRunArtifact } from './api/client'
import { FIXTURE_BASE, loadFixtureEvents, startReplay, type Replay } from './api/replay'
import { subscribeToRun, type Subscription } from './api/sse'
import ApprovalModal from './components/ApprovalModal'
import FactorList from './components/FactorList'
import IntakeForm, { PRESETS, type Preset } from './components/IntakeForm'
import MapView, { type GridCell } from './components/MapView'
import Plan from './components/Plan'
import SiteCards from './components/SiteCards'
import { initialRunState, runReducer, type RunStatus } from './state/runReducer'
import type { PracticeProfile } from './types/contracts'

const DEFAULT_MODE: Mode = (import.meta.env.VITE_FIXTURE_MODE ?? '1') === '1' ? 'fixture' : 'live'
type Mode = 'fixture' | 'live'

const STATUS_TEXT: Record<RunStatus, string> = {
  idle: 'Ready',
  running: 'Working',
  waiting: 'Waiting for your approval',
  done: 'Done',
  error: 'Stopped',
}

export default function App() {
  const [state, dispatch] = useReducer(runReducer, initialRunState)
  const [grid, setGrid] = useState<GridCell[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)
  const [preset, setPreset] = useState<Preset>(PRESETS[0])
  const [profile, setProfile] = useState<PracticeProfile>(PRESETS[0].profile)
  const [selectedSite, setSelectedSite] = useState<string | null>(null)
  const [mode, setMode] = useState<Mode>(DEFAULT_MODE)
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
    return () => {
      replay.current?.stop()
      live.current?.close()
    }
  }, [])

  const start = useCallback(async () => {
    replay.current?.stop()
    live.current?.close()
    setSelectedSite(null)
    setLoadError(null)
    try {
      if (fixtureMode) {
        const events = await loadFixtureEvents()
        replay.current = startReplay(events, dispatch, { runId: 'fixture-replay' })
        return
      }
      const { run_id: runId } = await createRun(profile)
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
      setLoadError((e as Error).message)
    }
  }, [fixtureMode, profile])

  const answerApproval = useCallback(
    async (decision: 'approve' | 'reject') => {
      const approval = state.approval
      if (!approval) return
      if (fixtureMode) {
        replay.current?.answer(approval.approval_id)
        return
      }
      try {
        await approve(state.runId!, { approval_id: approval.approval_id, decision })
        dispatch({ type: 'approvalAnswered', approvalId: approval.approval_id })
      } catch (e) {
        setLoadError((e as Error).message)
      }
    },
    [fixtureMode, state.approval, state.runId],
  )

  const reset = useCallback(() => {
    replay.current?.stop()
    replay.current = null
    live.current?.close()
    live.current = null
    setSelectedSite(null)
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
  const sites = state.report?.recommendations ?? []
  const practiceName = state.report?.profile.name ?? (state.status === 'idle' ? null : fixtureMode ? 'Recorded run' : profile.name)

  return (
    <div className="app">
      <header className="header">
        <div className="wordmark">
          <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden="true">
            <path d="M16 2l12 7v14l-12 7-12-7V9z" fill="#1F5866" />
            <path d="M16 9l6 3.5v7L16 23l-6-3.5v-7z" fill="#E39B2D" />
          </svg>
          Siteline
        </div>
        {practiceName && <span className="practice">{practiceName}</span>}
        <span className="spacer" />
        {state.status === 'idle' ? (
          <div className="modes" role="group" aria-label="Data source">
            <button className="button" aria-pressed={fixtureMode} onClick={() => setMode('fixture')}>
              Recorded run
            </button>
            <button className="button" aria-pressed={!fixtureMode} onClick={() => setMode('live')}>
              Live agent
            </button>
          </div>
        ) : (
          <span className="mode">{fixtureMode ? 'Recorded run' : 'Live agent'}</span>
        )}
        <span className="status" data-state={state.status} role="status">
          <span className="dot" />
          {STATUS_TEXT[state.status]}
        </span>
        {state.status !== 'idle' && (
          <button className="button" onClick={reset}>
            New search
          </button>
        )}
      </header>

      <main className="main">
        <aside className="panel left">
          {state.status === 'idle' ? (
            <IntakeForm
              profile={profile}
              presetId={preset.id}
              fixtureMode={fixtureMode}
              onPreset={(p) => {
                setPreset(p)
                setProfile(p.profile)
              }}
              onChange={setProfile}
              onSubmit={start}
            />
          ) : (
            <Plan todos={state.todos} scouts={scouts} factors={state.factors} trace={state.trace} />
          )}
        </aside>

        <section className="mapwrap">
          {(loadError || state.error) && <p className="banner">{loadError ?? state.error}</p>}
          <MapView
            grid={grid}
            zones={state.zones}
            sites={sites}
            featureLabels={featureLabels}
            selectedSiteId={selectedSite}
            onSelectSite={setSelectedSite}
          />
          {state.report && (
            <SiteCards report={state.report} featureLabels={featureLabels} selectedSiteId={selectedSite} onSelect={setSelectedSite} />
          )}
        </section>

        <aside className="panel right">
          <FactorList factors={factors} report={state.report} />
        </aside>
      </main>

      {state.approval && (
        <ApprovalModal
          approval={state.approval}
          featureLabels={featureLabels}
          readOnly={fixtureMode}
          onApprove={() => answerApproval('approve')}
          onReject={() => answerApproval('reject')}
        />
      )}
    </div>
  )
}
