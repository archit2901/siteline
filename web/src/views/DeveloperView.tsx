// Developer view (#/dev): everything the agent does, for judges and power users.
import { useCallback, useMemo, useState } from 'react'
import ApprovalPanel from '../components/ApprovalModal'
import Ask from '../components/Ask'
import ChatIntake from '../components/ChatIntake'
import FactorList from '../components/FactorList'
import Icon, { Mark } from '../components/Icon'
import IntakeForm, { type Source } from '../components/IntakeForm'
import MapView from '../components/MapView'
import { BusinessProfile, ReportNotes } from '../components/Profile'
import RunLine, { BRANCH_COLORS } from '../components/RunLine'
import { ResultsTable, SiteFlyer } from '../components/SiteCards'
import type { RunStatus } from '../state/runReducer'
import type { Siteline } from '../state/useSiteline'

const LANGSMITH_URL = import.meta.env.VITE_LANGSMITH_URL as string | undefined

const STATUS_TEXT: Record<RunStatus, string> = {
  idle: 'Ready',
  running: 'Working',
  waiting: 'Waiting for your approval',
  done: 'Done',
  error: 'Stopped',
}
const SOURCE_TEXT: Record<Source, string> = { live: 'Live agent', recorded: 'Recorded run', offline: 'Offline demo' }

type Tab = 'site' | 'ask' | 'factors' | 'sources'

export default function DeveloperView({ s }: { s: Siteline }) {
  const { state, report, sites, featureLabels, factors, source, profile, preset, golden } = s
  const [tab, setTab] = useState<Tab>('site')
  const selectSite = useCallback(
    (id: string) => {
      s.selectSite(id)
      setTab('site')
    },
    [s],
  )
  const scouts = useMemo(() => state.scoutOrder.map((id) => state.scouts[id]), [state.scoutOrder, state.scouts])
  const scoutColors = useMemo(
    () => Object.fromEntries(state.scoutOrder.map((id, k) => [id, BRANCH_COLORS[k % BRANCH_COLORS.length]])),
    [state.scoutOrder],
  )
  const selectedRec = sites.find((r) => r.site.id === s.selectedSite)
  const recordedAvailable = golden.includes(preset.id)

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
            {(['live', 'recorded', 'offline'] as Source[]).map((src) => (
              <button
                key={src}
                aria-pressed={source === src}
                disabled={src === 'recorded' && !recordedAvailable}
                title={src === 'recorded' && !recordedAvailable ? 'No recorded run of this example on the server' : undefined}
                onClick={() => s.setSource(src)}
              >
                {SOURCE_TEXT[src]}
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
          <button className="button" onClick={s.reset}>
            <Icon name="back" />
            New search
          </button>
        )}
        <a className="c-dev" href="#/">
          Customer view
        </a>
      </header>

      <main className={`main${report ? ' done' : ''}`}>
        <aside className="panel left" aria-label={idle ? 'Describe the business' : 'The run'}>
          {idle && source === 'live' ? (
            <ChatIntake onSubmit={(p) => s.start(p)} />
          ) : idle ? (
            <IntakeForm
              profile={profile}
              presetId={preset.id}
              source={source}
              recordedIds={golden}
              onPreset={s.choosePreset}
              onChange={s.setProfile}
              onSubmit={() => s.start()}
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
            {(s.loadError || state.error) && <p className="banner">{s.loadError ?? state.error}</p>}
            <MapView
              grid={s.grid}
              zones={state.zones}
              sites={sites}
              featureLabels={featureLabels}
              selectedSiteId={s.selectedSite}
              onSelectSite={selectSite}
              demandLabel={report?.demand_label}
              trip={report?.demand?.trip ?? null}
              focusSelection={s.userPicked}
            />
            {state.approval && (
              <ApprovalPanel
                approval={state.approval}
                featureLabels={featureLabels}
                readOnly={source !== 'live'}
                recorded={source === 'recorded'}
                onApprove={s.answerApproval}
                onReject={() => state.approval && s.answerApproval({ approval_id: state.approval.approval_id, decision: 'reject' })}
              />
            )}
          </div>
          {report && (
            <ResultsTable
              report={report}
              selectedSiteId={s.selectedSite}
              onSelect={selectSite}
              tune={source === 'live' && s.spec ? { spec: s.spec, featureLabels, busy: s.tuning, onChange: s.retune } : undefined}
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
                    ['ask', 'Ask'],
                    ['factors', 'Factors'],
                    ['sources', 'Sources'],
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
                {tab === 'ask' && <Ask report={report} onSelect={selectSite} />}
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
