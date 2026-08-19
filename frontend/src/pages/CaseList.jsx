import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { listCases } from '../api'
import Seal, { resolveCaseSeal } from '../components/Seal.jsx'

function formatDate(iso) {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleString('zh-TW', { hour12: false })
  } catch {
    return iso
  }
}

export default function CaseList() {
  const [cases, setCases] = useState(null)
  const [error, setError] = useState('')
  const navigate = useNavigate()
  const timerRef = useRef(null)

  const load = useCallback(async () => {
    try {
      const data = await listCases()
      setCases(data)
      setError('')
    } catch (err) {
      setError(err.message || '案件清單載入失敗,請重新整理頁面。')
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    const hasProcessing = cases?.some((c) => c.status === 'processing')
    if (hasProcessing) {
      timerRef.current = setTimeout(load, 3000)
    }
    return () => clearTimeout(timerRef.current)
  }, [cases, load])

  return (
    <div className="content">
      <div className="page-header">
        <h1 className="page-header__title">案件清單</h1>
        <div className="page-header__actions">
          <button type="button" className="btn btn-primary" onClick={() => navigate('/new')}>
            新建案件
          </button>
        </div>
      </div>

      {error && <div className="state-message state-message--error">{error}</div>}

      {!error && cases === null && <div className="state-message">載入中…</div>}

      {!error && cases !== null && cases.length === 0 && (
        <div className="state-message">尚無案件。點選右上「新建案件」開始。</div>
      )}

      {!error && cases !== null && cases.length > 0 && (
        <div className="table-wrap">
          <table className="list-table">
            <thead>
              <tr>
                <th>案號</th>
                <th>標題</th>
                <th>案類</th>
                <th>狀態</th>
                <th>建立時間</th>
              </tr>
            </thead>
            <tbody>
              {cases.map((c) => {
                const seal = resolveCaseSeal(c)
                return (
                  <tr
                    key={c.case_id}
                    tabIndex={0}
                    role="link"
                    aria-label={`檢視案件 ${c.title || c.case_id}`}
                    onClick={() => navigate(`/cases/${c.case_id}`)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        navigate(`/cases/${c.case_id}`)
                      }
                    }}
                  >
                    <td className="mono">{c.case_id}</td>
                    <td>{c.title || '(未命名案件)'}</td>
                    <td>{c.case_type || '—'}</td>
                    <td>
                      <Seal kind={seal.kind} size="sm">
                        {seal.text}
                      </Seal>
                    </td>
                    <td className="mono">{formatDate(c.created_at)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
