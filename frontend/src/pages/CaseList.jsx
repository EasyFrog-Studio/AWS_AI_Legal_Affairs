import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { listCases } from '../api'
import AppShell from '../components/AppShell.jsx'
import Icon from '../components/Icon.jsx'
import Seal, { resolveProgressSeal, resolveResultSeal } from '../components/Seal.jsx'
import './CaseList.css'

const STATUS_OPTIONS = ['全部', '審理中', '處理失敗', '已審結']
const RESULT_OPTIONS = ['全部', '不受理', '駁回', '撤銷另處', '原處分撤銷', '部分不受理部分駁回']
const ALL = '全部'
// 清單先看要處理的:失敗的要重收件、審理中的等承辦人動作,已審結的只是留存
const PROGRESS_ORDER = ['處理失敗', '審理中', '已審結']

const PERIOD_OPTIONS = [
  { label: ALL, days: null },
  { label: '3 天', days: 3 },
  { label: '7 天', days: 7 },
  { label: '14 天', days: 14 },
  { label: '1 個月', days: 30 },
  { label: '3 個月', days: 90 },
  { label: '半年', days: 180 },
]
const DAY_MS = 24 * 60 * 60 * 1000
const PAGE_SIZE = 20

function formatDate(iso) {
  if (!iso) return ''
  const at = new Date(iso)
  // 解析失敗時 toLocaleString 回 "Invalid Date" 而不拋錯,原字串才看得出資料壞在哪
  if (Number.isNaN(at.getTime())) return iso
  try {
    return at.toLocaleString('zh-TW', {
      hour12: false,
      year: 'numeric',
      month: 'numeric',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return iso
  }
}

export default function CaseList() {
  const [cases, setCases] = useState(null)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState(ALL)
  const [result, setResult] = useState(ALL)
  const [caseType, setCaseType] = useState(ALL)
  const [period, setPeriod] = useState(ALL)
  const [page, setPage] = useState(1)
  const navigate = useNavigate()
  const timerRef = useRef(null)

  const load = useCallback(async () => {
    try {
      const data = await listCases()
      setCases(data)
      setError('')
    } catch (err) {
      setError(err.message || '案件清單載入失敗，請重新整理頁面。')
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

  const isFiltering =
    search.trim() !== '' || status !== ALL || result !== ALL || caseType !== ALL || period !== ALL

  const filtered = useMemo(() => {
    if (!cases) return []
    const q = search.trim().toLowerCase()
    const days = PERIOD_OPTIONS.find((o) => o.label === period)?.days
    const since = days ? Date.now() - days * DAY_MS : null
    const rows = cases.filter((c) => {
      if (q) {
        const inId = (c.case_id || '').toLowerCase().includes(q)
        const inTitle = (c.title || '').toLowerCase().includes(q)
        if (!inId && !inTitle) return false
      }
      if (status !== ALL && resolveProgressSeal(c).text !== status) return false
      if (result !== ALL && resolveResultSeal(c)?.text !== result) return false
      if (caseType !== ALL && c.case_type !== caseType) return false
      if (since !== null) {
        const at = Date.parse(c.created_at)
        if (Number.isNaN(at) || at < since) return false
      }
      return true
    })
    // sort 穩定,同一進度內維持 API 給的時間序
    return rows.sort(
      (a, b) =>
        PROGRESS_ORDER.indexOf(resolveProgressSeal(a).text) -
        PROGRESS_ORDER.indexOf(resolveProgressSeal(b).text),
    )
  }, [cases, search, status, result, caseType, period])

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  // 篩選變動後筆數可能不夠翻到原本那頁,夾回範圍才不會停在空白頁
  const currentPage = Math.min(page, totalPages)
  const pageRows = filtered.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE)

  useEffect(() => {
    setPage(1)
  }, [search, status, result, caseType, period])

  function clearFilters() {
    setSearch('')
    setStatus(ALL)
    setResult(ALL)
    setCaseType(ALL)
    setPeriod(ALL)
  }

  const railSlot = (
    <div className="rail-section">
      <div className="rail-section__title case-list-filter-title">篩選</div>
      <div className="case-list-filter-group">
        <div className="rail-field-wrap">
          <Icon name="search" className="rail-field-wrap__icon" />
          <input
            type="text"
            aria-label="搜尋"
            placeholder="案號或檔名"
            className="rail-field"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
      </div>
      <div className="case-list-filter-group">
        <label htmlFor="case-list-status" className="rail-label">
          進度
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
        <label htmlFor="case-list-result" className="rail-label">
          結果
        </label>
        <select
          id="case-list-result"
          className="rail-field"
          value={result}
          onChange={(e) => setResult(e.target.value)}
        >
          {RESULT_OPTIONS.map((opt) => (
            <option key={opt} value={opt}>
              {opt}
            </option>
          ))}
        </select>
      </div>
      <div className="case-list-filter-group">
        <label htmlFor="case-list-type" className="rail-label">
          案件類別
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
      <div className="case-list-filter-group">
        <label htmlFor="case-list-period" className="rail-label">
          時間
        </label>
        <select
          id="case-list-period"
          className="rail-field"
          value={period}
          onChange={(e) => setPeriod(e.target.value)}
        >
          {PERIOD_OPTIONS.map((opt) => (
            <option key={opt.label} value={opt.label}>
              {opt.label}
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
            新增案件
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
                <th>案件類別</th>
                <th>結果</th>
                <th>進度</th>
                <th>建立時間</th>
              </tr>
            </thead>
            <tbody>
              {pageRows.map((c) => {
                const progressSeal = resolveProgressSeal(c)
                const resultSeal = resolveResultSeal(c)
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
                    <td>{c.case_type || '—'}</td>
                    <td>
                      {resultSeal ? (
                        <Seal kind={resultSeal.kind} size="sm">
                          {resultSeal.text}
                        </Seal>
                      ) : (
                        '—'
                      )}
                    </td>
                    <td>
                      <span className={`case-list-progress case-list-progress--${progressSeal.kind}`}>
                        {progressSeal.text}
                      </span>
                    </td>
                    <td className="mono">{formatDate(c.created_at)}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {!error && cases !== null && filtered.length > PAGE_SIZE && (
        <nav className="case-list-pager" aria-label="分頁">
          <button
            type="button"
            className="btn btn-secondary"
            disabled={currentPage <= 1}
            onClick={() => setPage(currentPage - 1)}
          >
            上一頁
          </button>
          <span className="case-list-pager__label">
            第 {currentPage} / {totalPages} 頁
          </span>
          <button
            type="button"
            className="btn btn-secondary"
            disabled={currentPage >= totalPages}
            onClick={() => setPage(currentPage + 1)}
          >
            下一頁
          </button>
        </nav>
      )}
    </AppShell>
  )
}
