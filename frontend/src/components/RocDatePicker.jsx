import { useEffect, useRef, useState } from 'react'

// 民國年月日三格選擇器。所有日期欄唯一的輸入方式——禁止手打:手打的寫法各異,期間計算與決定書
// 只認後端 dates.normalize_roc 的標準寫法「民國114年7月4日」,這裡輸出的就是它。
const ROC_RE = /^(?:中華民國|民國)?\s*(\d{1,3})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日/

export function parseRocDate(value) {
  const m = ROC_RE.exec((value || '').trim())
  if (!m) return null
  return { year: Number(m[1]), month: Number(m[2]), day: Number(m[3]) }
}

export function formatRocDate({ year, month, day }) {
  return `民國${year}年${month}月${day}日`
}

const THIS_ROC_YEAR = new Date().getFullYear() - 1911
const YEARS = Array.from({ length: THIS_ROC_YEAR + 1 }, (_, i) => THIS_ROC_YEAR + 1 - i)
const MONTHS = Array.from({ length: 12 }, (_, i) => i + 1)
const DAYS = Array.from({ length: 31 }, (_, i) => i + 1)
const EMPTY = { year: '', month: '', day: '' }

function partsOf(value) {
  const parsed = parseRocDate(value)
  return parsed ? { year: String(parsed.year), month: String(parsed.month), day: String(parsed.day) } : EMPTY
}

/**
 * props: { id, label, value, onChange, disabled }
 * value 為標準寫法字串或空字串;三格都選定才呼叫 onChange(標準寫法),清除呼叫 onChange('')。
 * 選到一半的格子暫存在元件內:父層在湊滿三格前收不到 onChange,prop 不會變,靠 prop 回填會把剛選的年打回空白。
 * 解析不出的非空原值:三格留空並在旁印「無法辨識:<原文>」,承辦人選定日期即覆寫。
 */
export default function RocDatePicker({ id, label, value, onChange, disabled = false }) {
  const [parts, setParts] = useState(() => partsOf(value))
  const syncedRef = useRef(value)
  useEffect(() => {
    if (value !== syncedRef.current) {
      syncedRef.current = value
      setParts(partsOf(value))
    }
  }, [value])

  const unreadable = Boolean(value) && !parseRocDate(value)

  const update = (key, raw) => {
    const next = { ...parts, [key]: raw }
    setParts(next)
    if (next.year && next.month && next.day) {
      const formatted = formatRocDate({ year: Number(next.year), month: Number(next.month), day: Number(next.day) })
      syncedRef.current = formatted
      onChange(formatted)
    } else if (value) {
      syncedRef.current = ''
      onChange('')
    }
  }

  const clear = () => {
    setParts(EMPTY)
    syncedRef.current = ''
    onChange('')
  }

  const select = (key, options, unit) => (
    <select
      id={key === 'year' ? id : undefined}
      className="input roc-date__part"
      aria-label={`${label}${unit}`}
      value={parts[key]}
      disabled={disabled}
      onChange={(e) => update(key, e.target.value)}
    >
      <option value="">—</option>
      {options.map((n) => (
        <option key={n} value={n}>
          {n}
        </option>
      ))}
    </select>
  )

  return (
    <div className="roc-date" role="group" aria-label={label}>
      <span className="roc-date__era">民國</span>
      {select('year', YEARS, '年')}
      <span>年</span>
      {select('month', MONTHS, '月')}
      <span>月</span>
      {select('day', DAYS, '日')}
      <span>日</span>
      {(value || parts.year || parts.month || parts.day) && !disabled && (
        <button type="button" className="btn-link roc-date__clear" onClick={clear}>
          清除
        </button>
      )}
      {unreadable && <span className="roc-date__unreadable">無法辨識：{value}</span>}
    </div>
  )
}
