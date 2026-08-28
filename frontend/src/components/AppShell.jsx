import { useRef } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { clearApiKey } from '../api'
import { NavGuardContext, confirmLeave } from '../navGuard.js'
import { fan } from '../art/facets.js'

// 左欄底紋:固定 seed 的靜態切面,三個色階只差 ΔL ≤ 6%
const RAIL_FACETS = fan({
  cx: 130,
  cy: 220,
  rays: 9,
  rings: 2,
  r0: 90,
  step: 170,
  seed: 11,
  weights: [40, 40, 20],
  jitter: 0.5,
  split: false,
})

export default function AppShell({ railSlot, children }) {
  const guardRef = useRef(null)
  const navigate = useNavigate()
  function handleLogout() {
    if (!confirmLeave(guardRef)) return
    clearApiKey()
    navigate('/login')
  }

  return (
    <NavGuardContext.Provider value={{ guardRef }}>
      <div className="shell">
        <aside className="rail">
          <svg
            className="rail__texture"
            viewBox="0 0 260 900"
            preserveAspectRatio="xMidYMin slice"
            aria-hidden="true"
            focusable="false"
          >
            {RAIL_FACETS.map((p, i) => (
              <polygon key={i} points={p.points} className={`rail__facet--${p.tone}`} />
            ))}
          </svg>
          <div className="rail__brand">訴願案件審理輔助系統</div>
          <nav className="rail__nav" aria-label="全域導覽">
            <NavLink
              to="/"
              end
              className={({ isActive }) => `rail__link ${isActive ? 'rail__link--active' : ''}`}
              onClick={(e) => confirmLeave(guardRef, e)}
            >
              案件清單
            </NavLink>
            <NavLink
              to="/new"
              className={({ isActive }) => `rail__link ${isActive ? 'rail__link--active' : ''}`}
              onClick={(e) => confirmLeave(guardRef, e)}
            >
              新建案件
            </NavLink>
          </nav>
          <div className="rail__page">{railSlot}</div>
          <div className="rail__footer">
            <button type="button" className="rail__logout" onClick={handleLogout}>
              登出
            </button>
          </div>
        </aside>
        <main className="content">{children}</main>
      </div>
    </NavGuardContext.Provider>
  )
}
