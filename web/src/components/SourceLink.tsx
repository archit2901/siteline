import { safeHref } from '../lib/safe'
import type { Report } from '../types/contracts'

interface Props {
  evidenceRef: string
  report: Report
  featureLabels: Record<string, string>
}

/** One evidence_ref ("feature:<id>@<h3>", "model:<name>@<site>", "site:<field>@<site>") as a visible source. */
export default function SourceLink({ evidenceRef, report, featureLabels }: Props) {
  const [kind, rest = ''] = evidenceRef.split(':', 2)
  const [name, at = ''] = rest.split('@', 2)

  if (kind === 'feature') {
    const href = safeHref(report.sources?.[name])
    const label = featureLabels[name] ?? name
    return href ? (
      <a className="source" href={href} target="_blank" rel="noreferrer" title={`${label}: ${href}`}>
        {sourceName(href)}
      </a>
    ) : (
      <span className="source model">{label}</span>
    )
  }
  if (kind === 'model') {
    return (
      <span className="source model" title={`Computed by Siteline's Huff model (${name}) for ${at}`}>
        Siteline model
      </span>
    )
  }
  if (kind === 'site') {
    const site = report.recommendations.find((r) => r.site.id === at)?.site
    const label = site?.source === 'costar' ? 'CoStar listing' : 'Example site'
    return (
      <span className="source model" title={`${name} from the ${label.toLowerCase()}`}>
        {label}
      </span>
    )
  }
  return <span className="source model">{evidenceRef}</span>
}

function sourceName(href: string): string {
  if (href.includes('/NummVBqZSIJKUeVR/')) return 'City of Houston'  // the City's ArcGIS Online organization
  const host = new URL(href).hostname
  if (host.endsWith('census.gov')) return 'U.S. Census'
  if (host.endsWith('cms.gov') || host.endsWith('hhs.gov')) return 'CMS'
  if (host.endsWith('ridemetro.org')) return 'Houston METRO'
  if (host.endsWith('openstreetmap.org')) return 'OpenStreetMap'
  if (host.endsWith('cdc.gov')) return 'CDC'
  return host.replace(/^www\./, '')
}
