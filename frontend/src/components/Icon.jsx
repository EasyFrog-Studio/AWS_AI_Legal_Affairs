/**
 * 線稿圖示(封閉清單,規則見 design.md)。
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
  // 天秤線稿:底座、柱、支點飾、樑、兩盤各三條鏈
  'scale-mark': (
    <g fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M70 168 H130 L124 158 H76 Z" />
      <path d="M100 158 V60" />
      <path d="M100 40 L106 50 L100 60 L94 50 Z" />
      <path d="M40 62 H160" />
      <path d="M50 62 L34 110 M50 62 L66 110 M50 62 V108" />
      <path d="M150 62 L134 110 M150 62 L166 110 M150 62 V108" />
      <path d="M30 112 H70 Q50 134 30 112 Z" />
      <path d="M130 112 H170 Q150 134 130 112 Z" />
    </g>
  ),
}

const VIEWBOX = {
  'scale-mark': '0 0 200 200',
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
