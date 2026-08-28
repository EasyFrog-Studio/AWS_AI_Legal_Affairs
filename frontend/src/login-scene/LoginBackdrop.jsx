import Facets from './art/Facets.jsx'
import { fan } from './art/facets.js'

// 頁面外圍:紙色大切面,靜態(色階 0 紙 / 1 卡紙 / 2 凹陷)
const PAGE = fan({
  cx: 960,
  cy: 540,
  rays: 14,
  rings: 3,
  r0: 260,
  step: 300,
  seed: 7,
  weights: [50, 30, 20],
  jitter: 0.5,
  split: false,
})

export default function LoginBackdrop() {
  return (
    <div className="login-backdrop" aria-hidden="true">
      <svg viewBox="0 0 1920 1080" preserveAspectRatio="xMidYMid slice" focusable="false">
        <Facets polys={PAGE} prefix="geo__p" />
      </svg>
    </div>
  )
}
