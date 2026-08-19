import { Navigate, NavLink, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { getApiKey, clearApiKey } from './api'
import Login from './pages/Login.jsx'
import CaseList from './pages/CaseList.jsx'
import NewCase from './pages/NewCase.jsx'
import CaseDetail from './pages/CaseDetail.jsx'

function RequireAuth({ children }) {
  const location = useLocation()
  if (!getApiKey()) {
    return <Navigate to="/login" state={{ from: location }} replace />
  }
  return children
}

function Masthead() {
  const navigate = useNavigate()

  function handleLogout() {
    clearApiKey()
    navigate('/login')
  }

  return (
    <header className="masthead">
      <span className="masthead__title">訴願案件審理輔助系統</span>
      <nav className="masthead__nav">
        <NavLink
          to="/"
          end
          className={({ isActive }) => `masthead__link ${isActive ? 'masthead__link--active' : ''}`}
        >
          案件清單
        </NavLink>
        <NavLink
          to="/new"
          className={({ isActive }) => `masthead__link ${isActive ? 'masthead__link--active' : ''}`}
        >
          新建案件
        </NavLink>
        <button type="button" className="masthead__logout" onClick={handleLogout}>
          登出
        </button>
      </nav>
    </header>
  )
}

function AppShell({ children }) {
  return (
    <>
      <Masthead />
      {children}
    </>
  )
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        path="/"
        element={
          <RequireAuth>
            <AppShell>
              <CaseList />
            </AppShell>
          </RequireAuth>
        }
      />
      <Route
        path="/new"
        element={
          <RequireAuth>
            <AppShell>
              <NewCase />
            </AppShell>
          </RequireAuth>
        }
      />
      <Route
        path="/cases/:id"
        element={
          <RequireAuth>
            <AppShell>
              <CaseDetail />
            </AppShell>
          </RequireAuth>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
