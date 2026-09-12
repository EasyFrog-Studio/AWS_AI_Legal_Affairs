import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { createCase } from '../api'
import AppShell from '../components/AppShell.jsx'
import DropZone from '../components/DropZone.jsx'
import { DOCUMENT_SLOTS as SLOTS } from '../components/documentSlots.js'
import './NewCase.css'

/** 單一文件槽:只收 PDF。 */
function DocumentSlotField({ slot, file, onFile, disabled }) {
  return (
    <div className="doc-slot">
      <div className="doc-slot__head">
        <span className="doc-slot__label">{slot.label}</span>
        <span className="doc-slot__flag">{slot.optional ? '選填' : '必填'}</span>
      </div>
      <DropZone
        slot={slot}
        fieldId={`doc-${slot.key}`}
        file={file}
        onFile={onFile}
        disabled={disabled}
      />
    </div>
  )
}

/** 建案頁只負責送出文件並取得 case_id;確認結果、重傳、開始分析都交給案件詳情頁——
 * 那裡本來就是狀態驅動渲染與輪詢的唯一位置,不在這裡另開一套。 */
export default function NewCase() {
  const [files, setFiles] = useState(() => Object.fromEntries(SLOTS.map((s) => [s.key, null])))
  const [caseId, setCaseId] = useState('')
  const [status, setStatus] = useState('idle') // idle | loading | error
  const [errorMsg, setErrorMsg] = useState('')
  const [errorField, setErrorField] = useState('')
  const [showMissing, setShowMissing] = useState(false)
  const navigate = useNavigate()

  // 選填槽不列入送出條件:送達證書非必備文書,收案時逼人補一份本來就不存在的卷證等於卡住流程
  const missing = SLOTS.filter((s) => !s.optional && !files[s.key])
  const canSubmit = missing.length === 0

  function updateSlot(key, file) {
    setFiles((prev) => ({ ...prev, [key]: file }))
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
        if (files[slot.key]) formData.append(`${slot.key}_file`, files[slot.key])
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
          請提供訴願書、原處分書與原處分機關的訴願答辯書 PDF；送達證書為選填，可事後補上。
        </p>
        {/* 缺件的後果要在收案時就講清楚:承辦人若以為系統照樣算了期間,不受理的漏判就看不出來 */}
        <p className="newcase__hint">
          未附送達證書時，本系統不計算訴願期間，一律視為未逾期，是否逾期須人工確認。
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
                file={files[slot.key]}
                onFile={(file) => updateSlot(slot.key, file)}
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
                <span className="form-result__hint">請確認檔案為可讀取的 PDF 後再試一次。</span>
              )}
            </div>
          )}
        </form>
      </div>
    </AppShell>
  )
}
