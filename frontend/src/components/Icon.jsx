/**
 * 線稿圖示(封閉清單,見 REDESIGN_BRIEF.md 1.7.9)。
 * 顏色一律由 currentColor 繼承,元件內不得寫任何顏色。
 * 裝飾用:aria-hidden + focusable=false,不加 <title>。
 */
const ICONS = {
  'fishtail-solid': (
    <path d="M2.5 2.5h11v4L8 12 2.5 6.5z" fill="currentColor" stroke="none" />
  ),
  'fishtail-accent': (
    <path d="M2.5 2.5h11v4L8 12 2.5 6.5z" fill="currentColor" stroke="none" />
  ),
  'fishtail-hollow': (
    <path d="M2.5 2.5h11v4L8 12 2.5 6.5z" fill="none" stroke="currentColor" strokeWidth="1.5" />
  ),
  'square-hollow': (
    <rect x="2.75" y="2.75" width="10.5" height="10.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
  ),
  'square-error': (
    <rect x="2.75" y="2.75" width="10.5" height="10.5" fill="currentColor" stroke="none" />
  ),
  search: (
    <>
      <circle cx="6.5" cy="6.5" r="4" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <line x1="9.5" y1="9.5" x2="13.5" y2="13.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </>
  ),
  'tree-mark': (
    <>
      <path d="M100 180 V70" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path d="M100 150 L70 120" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path d="M100 150 L130 120" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path d="M100 120 L75 90" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path d="M100 120 L125 90" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path d="M100 95 L100 60" fill="none" stroke="currentColor" strokeWidth="1.5" />
      <path
        d="M60 130 Q55 90 80 65 Q100 45 120 65 Q145 90 140 130 Q120 145 100 140 Q80 145 60 130 Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
      />
    </>
  ),
}

const VIEWBOX = {
  'tree-mark': '0 0 200 200',
}

export default function Icon({ name, className = '' }) {
  const body = ICONS[name]
  if (!body) return null
  const viewBox = VIEWBOX[name] || '0 0 16 16'
  return (
    <svg
      className={`icon icon--${name} ${className}`}
      viewBox={viewBox}
      aria-hidden="true"
      focusable="false"
    >
      {body}
    </svg>
  )
}
