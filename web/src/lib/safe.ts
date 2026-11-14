// Agent output (factor names, dataset URLs, claims) is untrusted text. React escapes JSX text, but raw HTML strings
// (deck.gl tooltips) and hrefs need these helpers.

const ENTITIES: Record<string, string> = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }

/** HTML-escape a value for interpolation into an HTML string. */
export function esc(value: unknown): string {
  return String(value ?? '').replace(/[&<>"']/g, (c) => ENTITIES[c])
}

/** The URL if it is http(s), else undefined (blocks javascript:, data:, and relative tricks). */
export function safeHref(url: unknown): string | undefined {
  if (typeof url !== 'string') return undefined
  try {
    const parsed = new URL(url)
    return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? parsed.href : undefined
  } catch {
    return undefined
  }
}
