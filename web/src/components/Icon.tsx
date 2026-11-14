// A small authored icon set: 20px grid, 2px round strokes.
const PATHS = {
  check: 'M4.5 10.5l3.5 3.5 7.5-8',
  external: 'M11 4h5v5M16 4l-7 7M14 11.5V16H4V6h4.5',
  play: 'M7 4.5l9 5.5-9 5.5z',
  cube: 'M10 2.8l6.5 3.6v7.2L10 17.2l-6.5-3.6V6.4zM3.5 6.4L10 10l6.5-3.6M10 10v7.2',
  back: 'M12.5 4.5L7 10l5.5 5.5',
  warn: 'M10 3.5l7 12.5H3zM10 8.5v3.5M10 14.2v.3',
  trace: 'M4 5h12M4 10h8M4 15h10',
} as const

export type IconName = keyof typeof PATHS

export default function Icon({ name, size = 18 }: { name: IconName; size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={PATHS[name]} fill={name === 'play' ? 'currentColor' : 'none'} />
    </svg>
  )
}

/** Siteline mark: a ringed station inside a hexagon (an H3 cell). */
export function Mark({ size = 28 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">
      <path d="M16 2l12.1 7v14L16 30 3.9 23V9z" fill="#15171c" />
      <circle cx="16" cy="16" r="6.5" fill="#fff" stroke="#d62839" strokeWidth="4" />
    </svg>
  )
}
