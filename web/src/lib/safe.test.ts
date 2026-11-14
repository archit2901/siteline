import { describe, expect, it } from 'vitest'
import { esc, safeHref } from './safe'

describe('esc', () => {
  it('escapes markup so it renders as text', () => {
    expect(esc('<img src=x onerror=alert(1)>')).toBe('&lt;img src=x onerror=alert(1)&gt;')
    expect(esc(`"a" & 'b'`)).toBe('&quot;a&quot; &amp; &#39;b&#39;')
    expect(esc(null)).toBe('')
  })
})

describe('safeHref', () => {
  it('keeps http(s) links only', () => {
    expect(safeHref('https://data.cms.gov/x')).toBe('https://data.cms.gov/x')
    expect(safeHref('javascript:alert(1)')).toBeUndefined()
    expect(safeHref('data:text/html,hi')).toBeUndefined()
    expect(safeHref('/relative')).toBeUndefined()
    expect(safeHref(undefined)).toBeUndefined()
  })
})
