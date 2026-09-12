import { useState } from 'react'
import { overrideScreening, reanalyzeCase } from '../api'
import { useAutosave } from '../hooks/useAutosave.js'
import AutoTextarea from '../components/AutoTextarea.jsx'

/** 程序審查結論常駐可改:自動判之後承辦人只做確認,那就必須改得動——
 * 否則自動判等於終局判斷。三欄自動儲存,不再收合成「推翻」表單。
 * 改動不自動重跑檢索(會覆蓋已編輯的草稿),另給底部「AI 生成」。 */
export function ScreeningSection({ caseData, onChanged }) {
  const { screening } = caseData
  const [genBusy, setGenBusy] = useState(false)
  const [genError, setGenError] = useState('')
  const [hintCaseId, setHintCaseId] = useState(null) // 停用時點了才說明為何按不下去;記案號讓提示不跨案殘留

  const processing = caseData.status === 'processing'

  const syncedFrom = {
    passed: screening.passed,
    clause: screening.matched_clause || '',
    reasoning: screening.reasoning || '',
  }

  const {
    value: { passed, clause, reasoning },
    setField,
    flush: flushPending,
    status: saveState,
    error: saveMessage,
  } = useAutosave({
    initial: syncedFrom,
    syncedFrom,
    resetKey: caseData.case_id,
    save: async (next) => {
      const body = {
        passed: next.passed,
        matched_clause: next.passed ? null : next.clause || null,
        reasoning: next.reasoning,
      }
      await overrideScreening(caseData.case_id, body)
      onChanged()
    },
  })

  function handlePassedChange(e) {
    setField('passed', e.target.value === 'pass', { immediate: true })
  }

  function handleClauseChange(value) {
    setField('clause', value)
  }

  function handleReasoningChange(value) {
    setField('reasoning', value)
  }

  async function handleGenerate() {
    if (genBusy) return
    if (!caseData.screening_stale || processing) {
      setHintCaseId(caseData.case_id)
      return
    }
    setHintCaseId(null)
    await flushPending()
    const hasDownstream = caseData.f2 || caseData.f2_refs || caseData.f3 || caseData.f4
    if (
      hasDownstream &&
      !window.confirm(
        '將依修改後的程序審查結論重跑參考依據與決定書草稿，草稿會被覆蓋（草稿會先存一版）。確定要繼續？',
      )
    ) {
      return
    }
    setGenBusy(true)
    setGenError('')
    try {
      await reanalyzeCase(caseData.case_id, 'f2')
      onChanged()
    } catch (err) {
      setGenError(err.message || 'AI 生成失敗，請重試。')
    } finally {
      setGenBusy(false)
    }
  }

  const fieldsDisabled = processing
  const generateDisabled = !caseData.screening_stale || genBusy || processing
  const generateHint = processing ? '分析進行中，請稍候' : '程序審查結論未變更，無需重新生成'

  const saveStatusText = {
    idle: '',
    saving: '儲存中…',
    saved: '已儲存',
    error: `儲存失敗：${saveMessage}`,
  }[saveState]

  return (
    <div className="card">
      {caseData.screening_system && (
        <div className="screening-result">
          <span className="doc-check">已由承辦人推翻</span>
        </div>
      )}
      {screening.review_note && (
        <p className="deadline__note">須人工確認：{screening.review_note}</p>
      )}
      {caseData.screening_system && (
        <p className="screening-result__reasoning">
          系統原判：{caseData.screening_system.passed ? '受理' : '不受理'}
          {caseData.screening_system.matched_clause
            ? `（${caseData.screening_system.matched_clause}）`
            : ''}
          。{caseData.screening_system.reasoning}
        </p>
      )}

      <div className="screening-form">
        <div className="field form-row">
          <label className="field__label" htmlFor="screening-passed">
            審查結論
          </label>
          <select
            id="screening-passed"
            className="input"
            value={passed ? 'pass' : 'reject'}
            onChange={handlePassedChange}
            disabled={fieldsDisabled}
          >
            <option value="pass">受理</option>
            <option value="reject">不受理</option>
          </select>
        </div>
        {!passed && (
          <div className="field form-row">
            <label className="field__label" htmlFor="screening-clause">
              審查適用條款
            </label>
            <input
              id="screening-clause"
              type="text"
              className="input"
              placeholder="如 77條第2款"
              value={clause}
              onChange={(e) => handleClauseChange(e.target.value)}
              onBlur={flushPending}
              disabled={fieldsDisabled}
            />
          </div>
        )}
        <div className="field form-row form-row--block">
          <label className="field__label" htmlFor="screening-reasoning">
            審查理由
          </label>
          <AutoTextarea
            id="screening-reasoning"
            value={reasoning}
            onChange={handleReasoningChange}
            onBlur={flushPending}
            disabled={fieldsDisabled}
          />
        </div>
        {saveStatusText && (
          <p className="screening-form__status" role="status">
            {saveStatusText}
          </p>
        )}
      </div>

      <div className="action-row action-row--end">
        <button
          type="button"
          className="btn btn-primary btn-submit"
          onClick={handleGenerate}
          aria-disabled={generateDisabled}
        >
          AI 生成
        </button>
      </div>
      {hintCaseId === caseData.case_id && generateDisabled && (
        <p className="action-hint" role="alert">
          {generateHint}
        </p>
      )}
      {genError && <div className="form-result form-result--error">{genError}</div>}
    </div>
  )
}

export const DEADLINE_DATES = [
  ['送達生效日', 'service_date'],
  ['期間末日', 'due_date'],
  ['機關收文日', 'filed_date'],
]

/** 期間認定:overdue 為 null 是「無從認定」,與「未逾期」是兩件事,不可合併呈現。 */
export function DeadlineSection({ deadline }) {
  if (!deadline) return null
  const verdict =
    deadline.overdue === null || deadline.overdue === undefined
      ? { text: '無從認定', modifier: 'unknown' }
      : deadline.overdue
        ? { text: '已逾期', modifier: 'overdue' }
        : { text: '未逾期', modifier: 'timely' }
  const dates = DEADLINE_DATES.filter(([, key]) => deadline[key])
  return (
    <div className="card">
      <div className="deadline__head">
        <span className="deadline__title">訴願期間</span>
        <span className={`deadline__verdict deadline__verdict--${verdict.modifier}`}>
          {verdict.text}
        </span>
      </div>
      {dates.length > 0 && (
        <dl className="deadline__dates">
          {dates.map(([label, key]) => (
            <div key={key}>
              <dt>{label}</dt>
              <dd className="mono">{deadline[key]}</dd>
            </div>
          ))}
        </dl>
      )}
      {deadline.detail && <p className="deadline__detail">{deadline.detail}</p>}
      {deadline.review_note && (
        <p className="deadline__note">須人工確認：{deadline.review_note}</p>
      )}
    </div>
  )
}
