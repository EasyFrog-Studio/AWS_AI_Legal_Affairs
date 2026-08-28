import { useEffect, useRef, useState } from 'react'
import usePaused from './usePaused.js'
import Facets from './art/Facets.jsx'
import Objects from './art/Objects.jsx'
import { fan } from '../art/facets.js'
import './scene.css'

// 進場動畫每個分頁只播一次;無 storage(隱私模式)則每次都播
const KEY = 'login-scene-entered'
function storage(fn) {
  try {
    return fn(sessionStorage)
  } catch {
    return null
  }
}

// 卡片切面:色階 0-2 橄欖、3-4 鼠尾草、5 赭金(閃動)。插畫區(y < 330)只留深色階,讓天秤成為主體
const ART_BOTTOM = 330
function centroidY(points) {
  const ys = points.split(' ').map((p) => Math.min(700, Math.max(0, +p.split(',')[1])))
  return ys.reduce((a, b) => a + b, 0) / ys.length
}
const CARD = fan({
  cx: 200,
  cy: 150,
  rays: 12,
  rings: 3,
  r0: 80,
  step: 130,
  seed: 3,
  weights: [22, 26, 22, 14, 6, 10],
}).map((p) => (p.tone >= 4 && centroidY(p.points) < ART_BOTTOM ? { ...p, tone: p.tone === 5 ? 2 : 3 } : p))
const GOLD = 5
const STRIKE_MS = 340 // 與 scene.css 的 ls-strike 同長
const MAX_KNOCKS = 8

export default function LoginScene() {
  const paused = usePaused()
  const [enter] = useState(() => storage((s) => s.getItem(KEY)) !== '1')
  const [knocks, setKnocks] = useState([]) // 每次敲擊一個 id:法槌以最後一個重播,每個 id 各有一圈落槌時的漣漪
  const lastKnockAt = useRef(-Infinity)

  useEffect(() => {
    storage((s) => s.setItem(KEY, '1'))
  }, [])

  // 點在卡片外 → 敲一下;敲擊進行中的點擊不打斷它(否則落槌與漣漪永遠到不了),連點就以 340ms 一次連敲
  useEffect(() => {
    const onClick = (e) => {
      if (e.target.closest?.('.login__card')) return
      const now = Date.now()
      if (now - lastKnockAt.current < STRIKE_MS) return
      lastKnockAt.current = now
      setKnocks((k) => [...k.slice(1 - MAX_KNOCKS), now])
    }
    document.addEventListener('click', onClick)
    return () => document.removeEventListener('click', onClick)
  }, [])

  return (
    <div
      className={`login-scene${paused ? ' is-paused' : ''}`}
      aria-hidden="true"
      data-enter={enter ? '1' : undefined}
    >
      <svg className="scene__geo" viewBox="0 0 400 700" preserveAspectRatio="xMidYMid slice" focusable="false">
        <Facets polys={CARD} prefix="geo__c" lit={GOLD} />
      </svg>
      <svg className="scene__objects" viewBox="0 0 400 320" preserveAspectRatio="xMidYMax meet" focusable="false">
        <Objects knocks={knocks} />
      </svg>
    </div>
  )
}
