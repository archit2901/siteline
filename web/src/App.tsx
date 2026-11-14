import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react'
import { FIXTURE_BASE, loadFixtureEvents, startReplay, type Replay } from './api/replay'
import ApprovalModal from './components/ApprovalModal'
import FactorList from './components/FactorList'
import IntakeForm, { PRESETS, type Preset } from './components/IntakeForm'
import MapView, { type GridCell } from './components/MapView'
import Plan from './components/Plan'
import SiteCards from './components/SiteCards'
import { initialRunState, runReducer, type RunStatus } from './state/runReducer'
import type { PracticeProfile } from './types/contracts'

const FIXTURE_MODE = (import.meta.env.VITE_FIXTURE_MODE ?? '1') === '1'

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
  const replay = useRef<Replay | null>(null)

  useEffect(() => {
    fetch(`${FIXTURE_BASE}/grid.json`)
      .then((r) => {
        if (!r.ok) throw new Error(`Could not load the Houston grid (HTTP ${r.status}). Run "npm run fixtures".`)
        return r.json() as Promise<GridCell[]>
      })
      .then(setGrid)
      .catch((e: Error) => setLoadError(e.message))
    return () => replay.current?.stop()
  }, [])

  const start = useCallback(async () => {
    replay.current?.stop()
    setSelectedSite(null)
    try {
      const events = await loadFixtureEvents()
      replay.current = startReplay(events, dispatch, { runId: 'fixture-replay' })
    } catch (e) {
      setLoadError((e as Error).message)
    }
  }, [])

  const reset = useCallback(() => {
    replay.current?.stop()
    replay.current = null
    setSelectedSite(null)
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
  const practiceName = state.report?.profile.name ?? (state.status === 'idle' ? null : FIXTURE_MODE ? 'Recorded run' : profile.name)

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
        {FIXTURE_MODE && <span className="mode">Fixture mode</span>}
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
              fixtureMode={FIXTURE_MODE}
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
          readOnly={FIXTURE_MODE}
          onApprove={() => replay.current?.answer(state.approval!.approval_id)}
        />
      )}
    </div>
  )
}
