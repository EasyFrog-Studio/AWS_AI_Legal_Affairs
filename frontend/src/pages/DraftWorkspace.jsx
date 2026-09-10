import { useEffect, useRef, useState } from 'react'
import { updateDraft, downloadDraftPdf, finalizeCase, getDecisionSkeleton } from '../api'
import { useNavGuard } from '../navGuard.js'
import './DraftWorkspace.css'


// 三段本文的欄位名 -> 可編輯欄位;其餘區塊照 kind 直接排版,版面定義只在後端一份
const FALLBACK_BLOCKS = [
  { kind: 'heading', text: '主　文' },
  { kind: 'slot', text: 'main_text' },
  { kind: 'heading', text: '事　實' },
  { kind: 'slot', text: 'fact' },
  { kind: 'heading', text: '理　由' },
  { kind: 'slot', text: 'reason' },
]

function DecisionBlock({ block, labelFor, fields }) {
  if (block.kind === 'blank') return <div className="decision__gap" />
  if (block.kind === 'title') return <h3 className="decision__title">{block.text}</h3>
  if (block.kind === 'heading') {
    return labelFor ? (
      <label htmlFor={`draft-${labelFor}`} className="decision__heading">
        {block.text}
      </label>
    ) : (
      <div className="decision__heading">{block.text}</div>
    )
  }
  if (block.kind === 'slot') return fields[block.text] ?? null
  return <p className="decision__body">{block.text}</p>
}

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

/** 決定書草稿工作台:切換階段只切 `hidden`、永不 unmount,textarea 的未儲存內容才能存活;只在 caseId 變更時重置。 */
export default function DraftWorkspace({
  caseId,
  draft,
  laws,
  cases,
  track,
  versionCount = 0,
  versionsTruncated = false,
  finalizedAt = null,
  onViewSource,
  onSaved,
  hidden,
}) {
  const [fact, setFact] = useState(draft.fact || '')
  const [reason, setReason] = useState(draft.reason || '')
  const [mainText, setMainText] = useState(draft.main_text || '')
  const [state, setState] = useState('idle') // idle | saving | saved | error
  const [skeleton, setSkeleton] = useState(null) // null=載入中, []=載入失敗, 其餘=版面區塊
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

  useEffect(() => {
    let cancelled = false
    setSkeleton(null)
    getDecisionSkeleton(caseId).then(
      (data) => !cancelled && setSkeleton(data.blocks),
      // 版面載不到就只顯示可編輯欄位,並在畫面上說明——不能讓它看起來像「決定書本來就長這樣」
      () => !cancelled && setSkeleton([]),
    )
    return () => {
      cancelled = true
    }
  }, [caseId])

  const dirty =
    fact !== (draft.fact || '') ||
    reason !== (draft.reason || '') ||
    mainText !== (draft.main_text || '')

  useNavGuard(dirty, '草稿有未儲存的修改,離開後將遺失。確定要離開?')

  function draftPatch() {
    // base_version 帶目前的版本數:伺服器端不符即回 409,擋掉兩個視窗互相無聲覆寫
    return { fact, reason, main_text: mainText, base_version: versionCount }
  }

  async function handleSave() {
    setState('saving')
    setMessage('')
    try {
      await updateDraft(caseId, draftPatch())
      setState('saved')
      setMessage('已儲存')
      onSaved?.()
    } catch (err) {
      setState('error')
      setMessage(err.message || '儲存失敗,請重試。')
      // 版本衝突:同步真實狀態,但不沖掉使用者剛打的字(他還要拿來比對差異)
      if (err.status === 409) onSaved?.()
    }
  }

  async function handleDownload() {
    try {
      if (dirty) await updateDraft(caseId, draftPatch())
      await downloadDraftPdf(caseId)
    } catch (err) {
      setState('error')
      setMessage(err.message || '下載失敗,請重試。')
      if (err.status === 409) onSaved?.()
    }
  }

  async function handleFinalize() {
    setState('saving')
    setMessage('')
    try {
      if (dirty) await updateDraft(caseId, draftPatch())
      const result = await finalizeCase(caseId)
      setState('saved')
      setMessage(`已標記定稿(${result.pdf_location || '已落地'})`)
      onSaved?.()
    } catch (err) {
      setState('error')
      setMessage(err.message || '定稿失敗,請重試。')
      if (err.status === 409) onSaved?.()
    }
  }

  const fields = {
    main_text: (
      <textarea
        id="draft-main_text"
        aria-label="主文"
        ref={mainTextRef}
        className="textarea"
        value={mainText}
        onChange={(e) => setMainText(e.target.value)}
        rows={2}
      />
    ),
    fact: (
      <textarea
        id="draft-fact"
        aria-label="事實"
        ref={factRef}
        className="textarea"
        value={fact}
        onChange={(e) => setFact(e.target.value)}
        rows={8}
      />
    ),
    reason: (
      <textarea
        id="draft-reason"
        aria-label="理由"
        ref={reasonRef}
        className="textarea"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        rows={10}
      />
    ),
  }

  return (
    <div className="draft-workspace" hidden={hidden}>
      <div className="draft-paper">
        {skeleton === null && <p className="state-message state-message--pending">版面載入中…</p>}
        {skeleton !== null && skeleton.length === 0 && (
          <p className="state-message state-message--error">
            決定書版面載入失敗,以下僅為可編輯欄位,不是完整決定書。
          </p>
        )}
        {/* 版面未到位時不先畫欄位:區塊數一變,textarea 的位置就變,React 會把它重新掛載,
            使用者正在打的字會消失 */}
        {(skeleton === null ? [] : skeleton.length === 0 ? FALLBACK_BLOCKS : skeleton).map((block, i, all) => (
          <DecisionBlock
            key={i}
            block={block}
            labelFor={all[i + 1]?.kind === 'slot' ? all[i + 1].text : null}
            fields={fields}
          />
        ))}
        {draft.cited_laws?.length > 0 && (
          <div className="draft-field">
            <span className="draft-paper__section-title">引用法條</span>
            <p className="mono">{draft.cited_laws.join('、')}</p>
          </div>
        )}
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
          {/* 定稿只是標記,不鎖:定稿後仍可修改,改了再存一版 */}
          <button type="button" className="btn btn-secondary" onClick={handleFinalize}>
            {finalizedAt ? '重新定稿' : '標記定稿'}
          </button>
          <span className="draft-actions__meta">
            已存 {versionCount} 版{versionsTruncated ? '(最舊版本已捨棄)' : ''}
            {finalizedAt ? ` · 定稿於 ${finalizedAt}` : ''}
          </span>
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
