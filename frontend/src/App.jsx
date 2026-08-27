import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { getApiKey } from './api'
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

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        path="/"
        element={
          <RequireAuth>
            <CaseList />
          </RequireAuth>
        }
      />
      <Route
        path="/new"
        element={
          <RequireAuth>
            <NewCase />
          </RequireAuth>
        }
      />
      <Route
        path="/cases/:id"
        element={
          <RequireAuth>
            <CaseDetail />
          </RequireAuth>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
