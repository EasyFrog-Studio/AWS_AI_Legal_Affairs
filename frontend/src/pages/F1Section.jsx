import { useEffect, useState } from 'react'
import { updateCaseInfo, reanalyzeCase } from '../api'
import { useAutosave } from '../hooks/useAutosave.js'
import AutoTextarea from '../components/AutoTextarea.jsx'
import RocDatePicker from '../components/RocDatePicker.jsx'
import { F1_GROUPS } from '../components/documentSlots.js'
import './F1Section.css'

const ALL_FIELDS = F1_GROUPS.flatMap((g) => g.sections.flatMap((s) => s.fields))
const LIST_FIELD_KEYS = new Set(ALL_FIELDS.filter((f) => f.kind === 'list').map((f) => f.key))

/** CaseInfo → 表單本地狀態:list 欄攤成一行一項的字串,編輯中不因每次分行重算而吃掉正在打的換行。 */
function toLocal(info) {
  const local = {}
  for (const field of ALL_FIELDS) {
    const value = info[field.key]
    local[field.key] = LIST_FIELD_KEYS.has(field.key) ? (value || []).join('\n') : (value ?? '')
  }
  return local
}

/** 表單本地狀態 → CaseInfo,list 欄存檔時才拆行、去空行。 */
function toInfo(local) {
  const info = {}
  for (const field of ALL_FIELDS) {
    const raw = local[field.key] ?? ''
    info[field.key] = LIST_FIELD_KEYS.has(field.key)
      ? raw
          .split('\n')
          .map((line) => line.trim())
          .filter(Boolean)
      : raw
  }
  return info
}

/** 這一欄與模型原本擷取值不同:承辦人改過,顯示琥珀色標記與原值。 */
function fieldEdited(field, local, system) {
  if (!system) return null
  const systemValue = LIST_FIELD_KEYS.has(field.key)
    ? (system[field.key] || []).join('\n')
    : (system[field.key] ?? '')
  return systemValue !== local[field.key] ? systemValue : null
}

const CONFIRM_TEXT =
  '將依修改後的案件資訊重跑程序審查、參考依據與決定書草稿，程序審查的人工修改與草稿都會被覆蓋（草稿會先存一版）。確定要繼續？'

/**
 * F1 擷取頁:五個文件分頁(訴願書/送達證書/原處分書/訴願答辯書/綜合判讀),欄位常駐輸入框、
 * 就地即時自動儲存(文字 1 秒 debounce + blur 立即送出,select/日期改變即送),底部一顆「AI 生成」
 * 依 f1_stale 決定是否重跑程序審查以下。
 */
export function F1Section({ caseData, onChanged }) {
  const [selectedTab, setSelectedTab] = useState(F1_GROUPS[0].key)
  const [aiBusy, setAiBusy] = useState(false)
  const [aiError, setAiError] = useState('')

  // 換了案件分頁回到第一頁(自動儲存本身的重置交給 hook)
  useEffect(() => {
    setSelectedTab(F1_GROUPS[0].key)
  }, [caseData.case_id])

  const {
    value: local,
    setField,
    flush,
    status,
    error: message,
  } = useAutosave({
    initial: toLocal(caseData.f1 || {}),
    syncedFrom: caseData.f1 ? toLocal(caseData.f1) : null,
    resetKey: caseData.case_id,
    save: async (nextLocal) => {
      await updateCaseInfo(caseData.case_id, toInfo(nextLocal))
      await onChanged()
    },
    serialize: (nextLocal) => JSON.stringify(toInfo(nextLocal)),
  })

  function handleTextChange(key, value) {
    setField(key, value)
  }

  function handleImmediateChange(key, value) {
    setField(key, value, { immediate: true })
  }

  const disabled = caseData.status === 'processing'
  const f1Ready = Boolean(caseData.f1)

  async function handleGenerate() {
    if (disabled || aiBusy) return
    if (!caseData.f1_stale) return
    if (caseData.screening != null && !window.confirm(CONFIRM_TEXT)) return
    await flush()
    setAiBusy(true)
    setAiError('')
    try {
      await reanalyzeCase(caseData.case_id, 'screening')
      await onChanged()
    } catch (err) {
      setAiError(err.message || 'AI 生成失敗，請重試。')
    } finally {
      setAiBusy(false)
    }
  }

  if (!f1Ready) {
    return (
      <div className="state-message state-message--pending">
        {caseData.status === 'processing' && caseData.current_stage === 'f1' ? '處理中…' : '尚未執行'}
      </div>
    )
  }

  const system = caseData.f1_system

  function renderControl(field) {
    const id = `f1-${field.key}`
    const value = local[field.key] ?? ''
    if (field.kind === 'date') {
      return (
        <RocDatePicker
          id={id}
          label={field.label}
          value={value}
          disabled={disabled}
          onChange={(v) => handleImmediateChange(field.key, v)}
        />
      )
    }
    if (field.kind === 'select') {
      return (
        <select
          id={id}
          className="input"
          value={value}
          disabled={disabled}
          onChange={(e) => handleImmediateChange(field.key, e.target.value)}
        >
          {field.options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      )
    }
    if (field.kind === 'long' || field.kind === 'list') {
      return (
        <AutoTextarea
          id={id}
          value={value}
          disabled={disabled}
          onChange={(v) => handleTextChange(field.key, v)}
          onBlur={flush}
        />
      )
    }
    return (
      <input
        id={id}
        type="text"
        className="input"
        value={value}
        disabled={disabled}
        onChange={(e) => handleTextChange(field.key, e.target.value)}
        onBlur={flush}
      />
    )
  }

  function renderField(field) {
    const originalValue = fieldEdited(field, local, system)
    const edited = originalValue !== null
    return (
      <div className={`f1-field ${edited ? 'f1-field--edited' : ''}`} key={field.key}>
        <label htmlFor={`f1-${field.key}`}>
          {field.label}
          {edited && <span className="f1-field__flag">已修改</span>}
        </label>
        {renderControl(field)}
        {edited && (
          <span className="f1-field__original" title={`模型原本擷取：${originalValue || '（空）'}`}>
            {originalValue || '（空）'}
          </span>
        )}
      </div>
    )
  }

  function groupEdited(group) {
    if (!system) return false
    return group.sections.some((section) =>
      section.fields.some((field) => fieldEdited(field, local, system) !== null),
    )
  }

  const currentGroup = F1_GROUPS.find((g) => g.key === selectedTab)
  const currentDoc = caseData.documents?.[currentGroup.key]
  const currentOcr = Boolean(currentDoc?.ocr)

  return (
    <div className="f1-section">
      {disabled && <p className="f1-section__note">分析進行中，此時不開放修改案件資訊。</p>}
      <div role="tablist" aria-label="卷證文件" className="stage-tabs">
        {F1_GROUPS.map((group) => {
          const doc = caseData.documents?.[group.key]
          return (
            <button
              type="button"
              role="tab"
              key={group.key}
              id={`f1-tab-${group.key}`}
              aria-selected={selectedTab === group.key}
              aria-controls={`f1-panel-${group.key}`}
              className={`stage-tab ${selectedTab === group.key ? 'stage-tab--current' : ''}`}
              onClick={() => setSelectedTab(group.key)}
            >
              {group.label}
              {doc?.ocr && <span className="stage-tab__flag">OCR</span>}
              {groupEdited(group) && <span className="stage-tab__flag">已修改</span>}
            </button>
          )
        })}
      </div>

      <div role="tabpanel" id={`f1-panel-${currentGroup.key}`} aria-labelledby={`f1-tab-${currentGroup.key}`}>
        {currentOcr && <p className="f1-ocr-note">本文件由 OCR 取字，日期請對照原件核對</p>}
        {currentGroup.sections.map((section) => (
          <fieldset className="f1-fieldset" key={section.label}>
            <legend>{section.label}</legend>
            <div className="f1-grid">{section.fields.map((field) => renderField(field))}</div>
          </fieldset>
        ))}
      </div>

      <div className="f1-status-row">
        <p role="status" className={`f1-save-status ${status === 'error' ? 'f1-save-status--error' : ''}`}>
          {status === 'saving' && '儲存中…'}
          {status === 'saved' && '已儲存'}
          {status === 'error' && `儲存失敗：${message}`}
        </p>
      </div>

      <div className="f1-actions">
        {aiError && <p className="form-result form-result--error">{aiError}</p>}
        <div className="action-row action-row--end">
          {!caseData.f1_stale && <span className="f1-actions__hint">案件資訊未變更</span>}
          <button
            type="button"
            className={`btn btn-primary btn-submit ${aiBusy ? 'btn--loading' : ''}`}
            aria-disabled={!caseData.f1_stale || aiBusy || disabled}
            onClick={handleGenerate}
          >
            {aiBusy && <span className="btn__spinner" aria-hidden="true" />}
            {aiBusy ? '生成中…' : 'AI 生成'}
          </button>
        </div>
      </div>
    </div>
  )
}

export default F1Section
