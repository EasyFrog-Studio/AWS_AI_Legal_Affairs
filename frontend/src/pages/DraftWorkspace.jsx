import { useEffect, useRef, useState } from 'react'
import { updateDraft, downloadDraftPdf } from '../api'
import { useNavGuard } from '../navGuard.js'
import './DraftWorkspace.css'

function BasisPanel({ laws, cases, track, onViewSource }) {
  return (
    <aside className="basis-panel" aria-label="承辦參考依據">
      {track !== 'inadmissible' && (
        <div className="basis-panel__group">
          <div className="basis-panel__heading">參考法規(F2)</div>
          {laws === null && (
            <div className="state-message state-message--pending">檢索中…</div>
          )}
          {laws !== null && laws.length === 0 && (
            <div className="state-message state-message--empty">未檢索到相關法規。</div>
          )}
          {laws &&
            laws.length > 0 &&
            laws.map((law, i) => (
              <div className="basis-item" key={i}>
                <span className="basis-item__title">
                  {law.law_name} 第 {law.article_no} 條
                </span>
                <span className="basis-item__meta">修正日期 {law.amend_date}</span>
                {law.source_key && (
                  <button
                    type="button"
                    className="btn-link"
                    onClick={() => onViewSource(law.source_key)}
                  >
                    原文
                  </button>
                )}
              </div>
            ))}
        </div>
      )}
      <div className="basis-panel__group">
        <div className="basis-panel__heading">參考案例(F3)</div>
        {cases === null && (
          <div className="state-message state-message--pending">檢索中…</div>
        )}
        {cases !== null && cases.length === 0 && (
          <div className="state-message state-message--empty">未檢索到相似案例。</div>
        )}
        {cases &&
          cases.length > 0 &&
          cases.map((c, i) => (
            <div className="basis-item" key={i}>
              <span className="basis-item__title">
                {c.year}年 {c.case_type} — {c.result}
              </span>
              <span className="basis-item__meta">
                案號 {c.case_no} · 條款 {c.appeal_article}
              </span>
              {c.source_key && (
                <button
                  type="button"
                  className="btn-link"
                  onClick={() => onViewSource(c.source_key)}
                >
                  原文
                </button>
              )}
            </div>
          ))}
      </div>
    </aside>
  )
}

/** textarea 高度隨內容伸展(禁止手動拖拉);hidden 時 scrollHeight 為 0,顯示時重算。 */
function useAutoGrow(ref, value, hidden) {
  useEffect(() => {
    const el = ref.current
    if (!el || hidden) return
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight}px`
  }, [ref, value, hidden])
}

/**
 * 決定書草稿工作台。切換左欄階段時,呼叫端只切換 `hidden`——這個元件永不 unmount,
 * 三個 textarea 的 useState 因此在切換階段時存活。只在 caseId 真的變更(換案件)時重置。
 */
export default function DraftWorkspace({
  caseId,
  draft,
  laws,
  cases,
  track,
  onViewSource,
  onSaved,
  hidden,
}) {
  const [fact, setFact] = useState(draft.fact || '')
  const [reason, setReason] = useState(draft.reason || '')
  const [mainText, setMainText] = useState(draft.main_text || '')
  const [state, setState] = useState('idle') // idle | saving | saved | error
  const [message, setMessage] = useState('')
  const prevCaseIdRef = useRef(caseId)
  const mainTextRef = useRef(null)
  const factRef = useRef(null)
  const reasonRef = useRef(null)
  useAutoGrow(mainTextRef, mainText, hidden)
  useAutoGrow(factRef, fact, hidden)
  useAutoGrow(reasonRef, reason, hidden)

  useEffect(() => {
    if (prevCaseIdRef.current !== caseId) {
      prevCaseIdRef.current = caseId
      setFact(draft.fact || '')
      setReason(draft.reason || '')
      setMainText(draft.main_text || '')
      setState('idle')
      setMessage('')
    }
  }, [caseId, draft])

  const dirty =
    fact !== (draft.fact || '') ||
    reason !== (draft.reason || '') ||
    mainText !== (draft.main_text || '')

  useNavGuard(dirty, '草稿有未儲存的修改,離開後將遺失。確定要離開?')

  async function handleSave() {
    setState('saving')
    setMessage('')
    try {
      await updateDraft(caseId, { fact, reason, main_text: mainText })
      setState('saved')
      setMessage('已儲存')
      onSaved?.()
    } catch (err) {
      setState('error')
      setMessage(err.message || '儲存失敗,請重試。')
    }
  }

  async function handleDownload() {
    try {
      if (dirty) await updateDraft(caseId, { fact, reason, main_text: mainText })
      await downloadDraftPdf(caseId)
    } catch (err) {
      setState('error')
      setMessage(err.message || '下載失敗,請重試。')
    }
  }

  return (
    <div className="draft-workspace" hidden={hidden}>
      <div className="draft-paper">
        <h3 className="draft-paper__title">{draft.draft_type}</h3>
        <div className="draft-field">
          <label htmlFor="draft-main-text" className="draft-paper__section-title">
            主文
          </label>
          <textarea
            id="draft-main-text"
            ref={mainTextRef}
            className="textarea"
            value={mainText}
            onChange={(e) => setMainText(e.target.value)}
            rows={2}
          />
        </div>
        <div className="draft-field">
          <label htmlFor="draft-fact" className="draft-paper__section-title">
            事實
          </label>
          <textarea
            id="draft-fact"
            ref={factRef}
            className="textarea"
            value={fact}
            onChange={(e) => setFact(e.target.value)}
            rows={8}
          />
        </div>
        <div className="draft-field">
          <label htmlFor="draft-reason" className="draft-paper__section-title">
            理由
          </label>
          <textarea
            id="draft-reason"
            ref={reasonRef}
            className="textarea"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            rows={10}
          />
        </div>
        <div className="draft-actions">
          <button
            type="button"
            className="btn btn-secondary"
            onClick={handleSave}
            disabled={state === 'saving' || !dirty}
          >
            {state === 'saving' ? '儲存中…' : '儲存修改'}
          </button>
          <button type="button" className="btn btn-primary" onClick={handleDownload}>
            下載 PDF 寄審
          </button>
          {message && (
            <span
              className={
                state === 'error' ? 'form-result form-result--error' : 'draft-actions__ok'
              }
            >
              {message}
            </span>
          )}
        </div>
      </div>
      <BasisPanel laws={laws} cases={cases} track={track} onViewSource={onViewSource} />
    </div>
  )
}
