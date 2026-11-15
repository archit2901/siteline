// One run, shared by the customer view and the developer view: switching views never restarts or loses a run.
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react'
import { AGENT_URL, approve, createRun, listGolden, loadRunArtifact, rescore } from '../api/client'
import { FIXTURE_BASE, loadFixtureEvents, startReplay, type Replay } from '../api/replay'
import { subscribeToRun, type Subscription } from '../api/sse'
import { PRESETS, type Preset, type Source } from '../components/IntakeForm'
import type { GridCell } from '../components/MapView'
import type { ApproveRequest, PracticeProfile, ScoringSpec } from '../types/contracts'
import { initialRunState, runReducer } from './runReducer'

const DEFAULT_SOURCE: Source = (import.meta.env.VITE_FIXTURE_MODE ?? '1') === '1' ? 'offline' : 'live'

export function useSiteline() {
  const [state, dispatch] = useReducer(runReducer, initialRunState)
  const [grid, setGrid] = useState<GridCell[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)
  const [preset, setPreset] = useState<Preset>(PRESETS[0])
  const [profile, setProfile] = useState<PracticeProfile>(PRESETS[0].profile)
  const [selectedSite, setSelectedSite] = useState<string | null>(null)
  const [source, setSource] = useState<Source>(DEFAULT_SOURCE)
  const [golden, setGolden] = useState<string[]>([])
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

  const choosePreset = useCallback(
    (p: Preset) => {
      setPreset(p)
      setProfile(p.profile)
      if (source === 'recorded' && !golden.includes(p.id)) setSource('live')
    },
    [source, golden],
  )

  /** Start a run: live from a profile, or a replay (recorded: the preset's golden run; offline: the fixture). */
  const start = useCallback(
    async (chosen?: PracticeProfile, recordedId?: string) => {
      const runProfile = chosen ?? profile
      if (chosen) setProfile(chosen)
      replay.current?.stop()
      live.current?.close()
      setSelectedSite(null)
      setUserPicked(false)
      setLoadError(null)
      try {
        if (source === 'offline') {
          const events = await loadFixtureEvents()
          replay.current = startReplay(events, dispatch, { runId: 'offline-demo' })
          return
        }
        const golden = source === 'recorded' ? recordedId ?? preset.id : undefined
        const { run_id: runId } = await createRun(runProfile, golden)
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
    },
    [source, profile, preset.id],
  )

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

  /** Tune weights after a live run: /rescore re-ranks the same spaces with no model call. */
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
  const report = state.report
  const sites = useMemo(() => report?.recommendations ?? [], [report])

  // The top-ranked space is selected when the report lands.
  useEffect(() => {
    if (report?.recommendations.length) setSelectedSite((cur) => cur ?? report.recommendations[0].site.id)
  }, [report])

  /** Select a space and fly the map to it. */
  const selectSite = useCallback((id: string) => {
    setSelectedSite(id)
    setUserPicked(true)
  }, [])
  /** Highlight a space (yellow marker) without moving the map, e.g. while hovering its card. */
  const highlightSite = useCallback((id: string) => setSelectedSite(id), [])

  return {
    state,
    grid,
    loadError,
    setLoadError,
    preset,
    profile,
    setProfile,
    choosePreset,
    source,
    setSource,
    golden,
    selectedSite,
    selectSite,
    highlightSite,
    userPicked,
    spec,
    tuning,
    start,
    answerApproval,
    retune,
    reset,
    factors,
    featureLabels,
    report,
    sites,
  }
}

export type Siteline = ReturnType<typeof useSiteline>
