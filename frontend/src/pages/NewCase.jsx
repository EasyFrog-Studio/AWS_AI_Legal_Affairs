import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { createCase } from '../api'
import AppShell from '../components/AppShell.jsx'
import { DOCUMENT_SLOTS as SLOTS } from '../components/documentSlots.js'
import './NewCase.css'

/** 單一文件槽:PDF / 文字二擇一,自己管自己的輸入狀態。 */
function DocumentSlotField({ slot, value, onChange, disabled }) {
  const fieldId = `doc-${slot.key}`
  return (
    <div className="doc-slot">
      <div className="doc-slot__head">
        <span className="doc-slot__label">{slot.label}</span>
        <span className="doc-slot__hint">{slot.hint}</span>
      </div>
      <div className="tabs tabs--sm">
        <button
          type="button"
          className={`tab ${value.tab === 'pdf' ? 'tab--active' : ''}`}
          onClick={() => onChange({ ...value, tab: 'pdf' })}
          disabled={disabled}
        >
          上傳 PDF
        </button>
        <button
          type="button"
          className={`tab ${value.tab === 'text' ? 'tab--active' : ''}`}
          onClick={() => onChange({ ...value, tab: 'text' })}
          disabled={disabled}
        >
          貼上文字
        </button>
      </div>
      {value.tab === 'pdf' && (
        <input
          id={fieldId}
          className="input"
          type="file"
          accept="application/pdf"
          onChange={(e) => onChange({ ...value, file: e.target.files?.[0] || null })}
          disabled={disabled}
        />
      )}
      {value.tab === 'text' && (
        <textarea
          id={fieldId}
          className="textarea"
          value={value.text}
          onChange={(e) => onChange({ ...value, text: e.target.value })}
          placeholder={`請貼上${slot.label}全文`}
          disabled={disabled}
          rows={4}
        />
      )}
    </div>
  )
}

const EMPTY_SLOT_VALUE = { tab: 'pdf', file: null, text: '' }

/** 建案頁只負責送出三份文件並取得 case_id;確認結果、重傳、開始分析都交給案件詳情頁——
 * 那裡本來就是狀態驅動渲染與輪詢的唯一位置,不在這裡另開一套。 */
export default function NewCase() {
  const [values, setValues] = useState(() =>
    Object.fromEntries(SLOTS.map((s) => [s.key, { ...EMPTY_SLOT_VALUE }])),
  )
  const [status, setStatus] = useState('idle') // idle | loading | error
  const [errorMsg, setErrorMsg] = useState('')
  const navigate = useNavigate()

  const canSubmit = SLOTS.every((s) => {
    const v = values[s.key]
    return v.tab === 'pdf' ? Boolean(v.file) : v.text.trim().length > 0
  })

  function updateSlot(key, next) {
    setValues((prev) => ({ ...prev, [key]: next }))
  }

  async function handleSubmit(e) {
    e.preventDefault()
    if (!canSubmit || status === 'loading') return
    setStatus('loading')
    setErrorMsg('')
    try {
      const formData = new FormData()
      for (const slot of SLOTS) {
        const v = values[slot.key]
        if (v.tab === 'pdf') {
          formData.append(`${slot.key}_file`, v.file)
        } else {
          formData.append(`${slot.key}_text`, v.text)
        }
      }
      const res = await createCase(formData)
      navigate(`/cases/${res.case_id}`)
    } catch (err) {
      setStatus('error')
      setErrorMsg(err.message || '送出失敗,請確認檔案格式後再試一次。')
    }
  }

  return (
    <AppShell>
      <div className="page-header">
        <h1 className="page-header__title">新建案件</h1>
      </div>

      <div className="newcase">
        <p className="newcase__intro">
          請分別提供訴願書、送達證書、原處分書三份文件(PDF 或貼上全文皆可),系統會先確認每份文件的類型,
          確認無誤後再依訴願法第 77 條進行程序審查並生成草稿。
        </p>

        <form onSubmit={handleSubmit}>
          {SLOTS.map((slot) => (
            <DocumentSlotField
              key={slot.key}
              slot={slot}
              value={values[slot.key]}
              onChange={(next) => updateSlot(slot.key, next)}
              disabled={status === 'loading'}
            />
          ))}

          <button
            type="submit"
            className={`btn btn-primary ${status === 'loading' ? 'btn--loading' : ''}`}
            disabled={!canSubmit || status === 'loading'}
          >
            {status === 'loading' && <span className="btn__spinner" aria-hidden="true" />}
            {status === 'loading' ? '確認文件中…' : '送出並確認文件'}
          </button>

          {status === 'error' && (
            <div className="form-result form-result--error">
              {errorMsg}
              <span className="form-result__hint">請確認檔案格式後再試一次,或改用文字貼上。</span>
            </div>
          )}
        </form>
      </div>
    </AppShell>
  )
}
