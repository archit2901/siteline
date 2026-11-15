// Customer view (default): describe the business, say yes twice, see the best spaces, ask questions.
// Everything else (the agent's internals) lives in the developer view (#/dev).
import { useEffect, useMemo, useState } from 'react'
import Ask from '../components/Ask'
import ChatIntake from '../components/ChatIntake'
import Icon, { Mark } from '../components/Icon'
import { PRESETS } from '../components/IntakeForm'
import MapView from '../components/MapView'
import ProgressFeed from '../components/ProgressFeed'
import ResultCard from '../components/ResultCard'
import SimpleApproval from '../components/SimpleApproval'
import { SiteFlyer } from '../components/SiteCards'
import type { Siteline } from '../state/useSiteline'

export default function CustomerView({ s }: { s: Siteline }) {
  const { state, report, sites, featureLabels, source, golden } = s
  const [showAll, setShowAll] = useState(false)
  const [drawer, setDrawer] = useState<string | null>(null)
  const scouts = useMemo(() => state.scoutOrder.map((id) => state.scouts[id]), [state.scoutOrder, state.scouts])
  const idle = state.status === 'idle'
  const done = Boolean(report)
  const shown = showAll ? sites : sites.slice(0, 3)
  const drawerRec = sites.find((r) => r.site.id === drawer)
  const what = report?.profile.business_type || report?.profile.specialty?.replace('_', ' ') || s.profile.business_type

  useEffect(() => {
    if (!report) {
      setShowAll(false)
      setDrawer(null)
    }
  }, [report])
  useEffect(() => {
    const close = (e: KeyboardEvent) => e.key === 'Escape' && setDrawer(null)
    window.addEventListener('keydown', close)
    return () => window.removeEventListener('keydown', close)
  }, [])

  return (
    <div className="customer">
      <header className="c-bar">
        <a className="wordmark" href="#/" onClick={() => idle || s.reset()}>
          <Mark />
          Siteline
        </a>
        {!idle && what && <span className="c-what">{what}</span>}
        <span className="spacer" />
        {source !== 'live' && <span className="chip">Demo: {source === 'recorded' ? 'recorded run' : 'offline'}</span>}
        {!idle && (
          <button className="button" onClick={s.reset}>
            <Icon name="back" />
            New search
          </button>
        )}
        <a className="c-dev" href="#/dev">
          Developer view
        </a>
      </header>

      {(s.loadError || state.error) && <p className="banner static">{s.loadError ?? state.error}</p>}

      {idle && (
        <main className="c-start">
          {source === 'live' ? (
            <ChatIntake onSubmit={(p) => s.start(p)} />
          ) : (
            <section className="section">
              <h1>Where in Houston should your business open next?</h1>
              <p className="lede">Demo mode: pick an example to replay a saved search.</p>
              <div className="suggest">
                {(source === 'offline' ? PRESETS.slice(0, 1) : PRESETS.filter((p) => golden.includes(p.id))).map((p) => (
                  <button key={p.id} className="preset" onClick={() => (s.choosePreset(p), s.start(p.profile, p.id))}>
                    <span className="ring" aria-hidden="true" />
                    {source === 'offline' ? 'Play the offline demo' : p.label}
                  </button>
                ))}
              </div>
            </section>
          )}
        </main>
      )}

      {!idle && !done && (
        <main className="c-work">
          <section className="c-progress">
            <h1>{state.status === 'waiting' ? 'I need your OK' : 'Finding your best spaces'}</h1>
            <ProgressFeed
              todos={state.todos}
              scouts={scouts}
              factors={state.factors}
              trace={state.trace}
              status={state.status}
              profileSummary={state.profileSummary}
            />
            {state.approval && (
              <SimpleApproval approval={state.approval} featureLabels={featureLabels} readOnly={source !== 'live'} onAnswer={s.answerApproval} />
            )}
          </section>
          <section className="c-map">
            <MapView grid={s.grid} zones={state.zones} sites={[]} featureLabels={featureLabels} selectedSiteId={null} onSelectSite={() => {}} />
          </section>
        </main>
      )}

      {done && report && (
        <main className="c-results">
          <section className="c-list">
            <h1>
              {sites.length ? `Your best ${Math.min(3, sites.length)} spaces in Houston` : 'No spaces fit yet'}
            </h1>
            {report.verified && (
              <p className="verified">
                <Icon name="check" />
                Every number was checked against its source.
              </p>
            )}
            {shown.map((rec) => (
              <ResultCard
                key={rec.site.id}
                rec={rec}
                report={report}
                selected={rec.site.id === s.selectedSite}
                onSelect={() => s.highlightSite(rec.site.id)}
                onDetails={() => (s.selectSite(rec.site.id), setDrawer(rec.site.id))}
              />
            ))}
            {sites.length > 3 && (
              <button className="button block" onClick={() => setShowAll((v) => !v)}>
                {showAll ? 'Show the top 3 only' : `See all ${sites.length} spaces`}
              </button>
            )}
            <Ask report={report} onSelect={(id) => (s.selectSite(id), setDrawer(id))} />
          </section>
          <section className="c-map sticky">
            <MapView
              grid={s.grid}
              zones={state.zones}
              sites={shown}
              featureLabels={featureLabels}
              selectedSiteId={s.selectedSite}
              onSelectSite={(id) => (s.selectSite(id), setDrawer(id))}
              demandLabel={report.demand_label}
              trip={report.demand?.trip ?? null}
              focusSelection={s.userPicked}
            />
          </section>
        </main>
      )}

      {drawerRec && report && (
        <div className="drawer-bg" onClick={() => setDrawer(null)}>
          <aside className="drawer" role="dialog" aria-modal="true" aria-label={`Details for ${drawerRec.site.address}`} onClick={(e) => e.stopPropagation()}>
            <button className="button drawer-close" onClick={() => setDrawer(null)}>
              Close
            </button>
            <SiteFlyer report={report} rec={drawerRec} featureLabels={featureLabels} onSelect={(id) => (s.selectSite(id), setDrawer(id))} />
          </aside>
        </div>
      )}
    </div>
  )
}
