import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { createCase } from '../api'
import AppShell from '../components/AppShell.jsx'
import AutoTextarea from '../components/AutoTextarea.jsx'
import DropZone from '../components/DropZone.jsx'
import { DOCUMENT_SLOTS as SLOTS } from '../components/documentSlots.js'
import './NewCase.css'

/** 單一文件槽:PDF / 文字二擇一,自己管自己的輸入狀態。 */
function DocumentSlotField({ slot, value, onChange, disabled }) {
  const fieldId = `doc-${slot.key}`
  return (
    <div className="doc-slot">
      <div className="doc-slot__head">
        <span className="doc-slot__label">{slot.label}</span>
        <span className="doc-slot__flag">{slot.optional ? '選填' : '必填'}</span>
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
        <DropZone
          slot={slot}
          fieldId={fieldId}
          file={value.file}
          onFile={(file) => onChange({ ...value, file })}
          disabled={disabled}
        />
      )}
      {value.tab === 'text' && (
        <AutoTextarea
          id={fieldId}
          className="doc-slot__text"
          value={value.text}
          onChange={(text) => onChange({ ...value, text })}
          placeholder={`請貼上${slot.label}全文`}
          disabled={disabled}
        />
      )}
    </div>
  )
}

const EMPTY_SLOT_VALUE = { tab: 'pdf', file: null, text: '' }

function isFilled(v) {
  return v.tab === 'pdf' ? Boolean(v.file) : v.text.trim().length > 0
}

/** 建案頁只負責送出三份文件並取得 case_id;確認結果、重傳、開始分析都交給案件詳情頁——
 * 那裡本來就是狀態驅動渲染與輪詢的唯一位置,不在這裡另開一套。 */
export default function NewCase() {
  const [values, setValues] = useState(() =>
    Object.fromEntries(SLOTS.map((s) => [s.key, { ...EMPTY_SLOT_VALUE }])),
  )
  const [caseId, setCaseId] = useState('')
  const [status, setStatus] = useState('idle') // idle | loading | error
  const [errorMsg, setErrorMsg] = useState('')
  const [errorField, setErrorField] = useState('')
  const [showMissing, setShowMissing] = useState(false)
  const navigate = useNavigate()

  // 選填槽不列入送出條件:機關的答辯書多半晚幾天才到,收案時逼人補件等於卡住整個流程
  const missing = SLOTS.filter((s) => !s.optional && !isFilled(values[s.key]))
  const canSubmit = missing.length === 0

  function updateSlot(key, next) {
    setValues((prev) => ({ ...prev, [key]: next }))
  }

  async function handleSubmit(e) {
    e.preventDefault()
    if (status === 'loading') return
    if (!canSubmit) {
      setShowMissing(true)
      return
    }
    setStatus('loading')
    setErrorMsg('')
    setErrorField('')
    try {
      const formData = new FormData()
      // 留白就整個不送:後端才是產案號的地方,前端不預先生成也不送空字串
      if (caseId.trim()) formData.append('case_id', caseId.trim())
      for (const slot of SLOTS) {
        const v = values[slot.key]
        // 空的選填槽整個不送:送一個空字串進去,後端會把它當「使用者貼了空白文字」
        if (!isFilled(v)) continue
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
      setErrorMsg(err.message || '送出失敗，請確認檔案格式後再試一次。')
      setErrorField(err.detail?.field || '')
    }
  }

  return (
    <AppShell>
      <div className="page-header page-header--tight">
        <h1 className="page-header__title">新增案件</h1>
      </div>

      <div className="newcase">
        <p className="doc-intro">
          請分別提供訴願書、送達證書、原處分書三份文件（限 PDF 或貼上全文皆可）；原處分機關的訴願答辯書可不附加
        </p>

        <form onSubmit={handleSubmit}>
          <div className="field newcase__caseid">
            <label className="field__label" htmlFor="case-id">
              案號（選填，未填視同隨機產生）
            </label>
            <input
              id="case-id"
              className="input"
              type="text"
              value={caseId}
              onChange={(e) => setCaseId(e.target.value)}
              placeholder="ex：1141091381"
              disabled={status === 'loading'}
            />
          </div>

          <div className="doc-grid">
            {SLOTS.map((slot) => (
              <DocumentSlotField
                key={slot.key}
                slot={slot}
                value={values[slot.key]}
                onChange={(next) => updateSlot(slot.key, next)}
                disabled={status === 'loading'}
              />
            ))}
          </div>

          <div className="action-row action-row--end">
            <button
              type="submit"
              className={`btn btn-primary btn-submit ${status === 'loading' ? 'btn--loading' : ''}`}
              // 用 aria-disabled 而非 disabled:原生停用的按鈕不吃 cursor,游標無法說明為何按不下去
              aria-disabled={!canSubmit || status === 'loading'}
            >
              {status === 'loading' && <span className="btn__spinner" aria-hidden="true" />}
              {status === 'loading' ? '確認文件中…' : '送出並確認文件'}
            </button>
          </div>

          {showMissing && missing.length > 0 && (
            <p className="newcase__missing" role="alert">
              尚未提供：{missing.map((s) => s.label).join('、')}
            </p>
          )}

          {status === 'error' && (
            <div className="form-result form-result--error">
              {errorMsg}
              {/* 案號被退回時,提示檔案格式只會把人帶偏 */}
              {errorField !== 'case_id' && (
                <span className="form-result__hint">請確認檔案格式後再試一次，或改用文字貼上。</span>
              )}
            </div>
          )}
        </form>
      </div>
    </AppShell>
  )
}
