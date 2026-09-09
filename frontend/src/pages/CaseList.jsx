import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { listCases } from '../api'
import AppShell from '../components/AppShell.jsx'
import Icon from '../components/Icon.jsx'
import Seal, { resolveCaseSeal } from '../components/Seal.jsx'
import './CaseList.css'

const STATUS_OPTIONS = [
  '全部',
  '待確認',
  '審理中',
  '待人工確認',
  '已審結',
  '不受理',
  '駁回',
  '原處分撤銷',
  '處理失敗',
]
const ALL = '全部'

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
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState(ALL)
  const [caseType, setCaseType] = useState(ALL)
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

  const caseTypeOptions = useMemo(() => {
    if (!cases) return []
    const set = new Set()
    cases.forEach((c) => {
      if (c.case_type) set.add(c.case_type)
    })
    return Array.from(set).sort()
  }, [cases])

  const isFiltering = search.trim() !== '' || status !== ALL || caseType !== ALL

  const filtered = useMemo(() => {
    if (!cases) return []
    const q = search.trim().toLowerCase()
    return cases.filter((c) => {
      if (q) {
        const inId = (c.case_id || '').toLowerCase().includes(q)
        const inTitle = (c.title || '').toLowerCase().includes(q)
        if (!inId && !inTitle) return false
      }
      if (status !== ALL && resolveCaseSeal(c).text !== status) return false
      if (caseType !== ALL && c.case_type !== caseType) return false
      return true
    })
  }, [cases, search, status, caseType])

  function clearFilters() {
    setSearch('')
    setStatus(ALL)
    setCaseType(ALL)
  }

  const railSlot = (
    <div className="rail-section">
      <div className="rail-section__title">篩選</div>
      <div className="case-list-filter-group">
        <div className="rail-field-wrap">
          <Icon name="search" className="rail-field-wrap__icon" />
          <input
            type="text"
            aria-label="搜尋"
            placeholder="案號或標題"
            className="rail-field"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
      </div>
      <div className="case-list-filter-group">
        <label htmlFor="case-list-status" className="rail-label">
          狀態
        </label>
        <select
          id="case-list-status"
          className="rail-field"
          value={status}
          onChange={(e) => setStatus(e.target.value)}
        >
          {STATUS_OPTIONS.map((opt) => (
            <option key={opt} value={opt}>
              {opt}
            </option>
          ))}
        </select>
      </div>
      <div className="case-list-filter-group">
        <label htmlFor="case-list-type" className="rail-label">
          案類
        </label>
        <select
          id="case-list-type"
          className="rail-field"
          value={caseType}
          onChange={(e) => setCaseType(e.target.value)}
        >
          <option value={ALL}>全部</option>
          {caseTypeOptions.map((opt) => (
            <option key={opt} value={opt}>
              {opt}
            </option>
          ))}
        </select>
      </div>
    </div>
  )

  return (
    <AppShell railSlot={railSlot}>
      <div className="page-header">
        <h1 className="page-header__title">案件清單</h1>
        {cases !== null && !error && (
          <span className="page-header__count">
            共 {cases.length} 件{isFiltering ? ` · 顯示 ${filtered.length} 件` : ''}
          </span>
        )}
      </div>

      {error && (
        <div className="state-message state-message--error">
          <div className="case-list-empty-text">{error}</div>
          <button type="button" className="btn btn-secondary" onClick={load}>
            重新載入
          </button>
        </div>
      )}

      {!error && cases === null && <div className="state-message">載入中…</div>}

      {!error && cases !== null && cases.length === 0 && (
        <div className="state-message">
          <div className="case-list-watermark">
            <Icon name="scale-mark" />
          </div>
          <div className="case-list-empty-text">尚無案件。</div>
          <button type="button" className="btn btn-primary" onClick={() => navigate('/new')}>
            新建案件
          </button>
        </div>
      )}

      {!error && cases !== null && cases.length > 0 && filtered.length === 0 && (
        <div className="state-message">
          <div className="case-list-empty-text">目前篩選條件下沒有案件。</div>
          <button type="button" className="btn-link" onClick={clearFilters}>
            清除篩選
          </button>
        </div>
      )}

      {!error && cases !== null && filtered.length > 0 && (
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
              {filtered.map((c) => {
                const seal = resolveCaseSeal(c)
                return (
                  <tr
                    key={c.case_id}
                    tabIndex={0}
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
    </AppShell>
  )
}
