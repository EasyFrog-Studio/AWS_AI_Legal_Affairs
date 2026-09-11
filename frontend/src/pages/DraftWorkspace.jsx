import { useEffect, useMemo, useRef, useState } from 'react'
import { updateDraftText, downloadDraftPdf, downloadDraftDocx, finalizeCase } from '../api'
import AutoTextarea from '../components/AutoTextarea.jsx'
import SourceSiteLink from '../components/SourceSiteLink.jsx'
import { useNavGuard } from '../navGuard.js'
import { splitDraft, joinDraft } from './draftSections.js'
import './DraftWorkspace.css'

const SECTION_ARIA = { head: '決定書表頭', main: '主文', fact: '事實', reason: '理由', tail: '決定書結尾' }


function BasisPanel({ laws, refs, cases, track, onViewSource }) {
  return (
    <aside className="basis-panel" aria-label="承辦參考依據">
      {track !== 'inadmissible' && (
        <div className="basis-panel__group">
          <div className="basis-panel__heading">參考法規（F2）</div>
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
                <SourceSiteLink url={law.source_url} />
              </div>
            ))}
        </div>
      )}
      {/* 參考見解兩條 track 都跑,故沒有 track 條件;但它不進 F4 的可引用清單,標題要與法規分開 */}
      <div className="basis-panel__group">
        <div className="basis-panel__heading">參考見解（F2+）</div>
        {refs === null && <div className="state-message state-message--pending">檢索中…</div>}
        {refs !== null && refs !== undefined && refs.length === 0 && (
          <div className="state-message state-message--empty">未檢索到相關參考見解。</div>
        )}
        {refs &&
          refs.length > 0 &&
          refs.map((ref, i) => (
            <div className="basis-item" key={i}>
              <span className="basis-item__title">{ref.name}</span>
              <span className="basis-item__meta">
                {ref.doc_kind}
                {ref.issuer ? ` · ${ref.issuer}` : ''} · {ref.issued_date}
              </span>
              {ref.source_key && (
                <button
                  type="button"
                  className="btn-link"
                  onClick={() => onViewSource(ref.source_key)}
                >
                  原文
                </button>
              )}
              <SourceSiteLink url={ref.source_url} />
            </div>
          ))}
      </div>
      <div className="basis-panel__group">
        <div className="basis-panel__heading">參考案例（F3）</div>
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
              <SourceSiteLink url={c.source_url} />
            </div>
          ))}
      </div>
    </aside>
  )
}

/** 決定書草稿工作台:切換階段只切 `hidden`、永不 unmount,textarea 的未儲存內容才能存活;只在 caseId 變更時重置。 */
export default function DraftWorkspace({
  caseId,
  draft,
  text = '',
  laws,
  refs,
  cases,
  track,
  versionCount = 0,
  versionsTruncated = false,
  finalizedAt = null,
  onViewSource,
  onSaved,
  hidden,
}) {
  const [plain, setPlain] = useState(text || '')
  const [state, setState] = useState('idle') // idle | saving | saved | error
  const [message, setMessage] = useState('')
  const prevCaseIdRef = useRef(caseId)
  const syncedPlainRef = useRef(text || '')

  useEffect(() => {
    if (prevCaseIdRef.current !== caseId) {
      prevCaseIdRef.current = caseId
      setPlain(text || '')
      syncedPlainRef.current = text || ''
      setState('idle')
      setMessage('')
    }
  }, [caseId, draft])

  // 重跑會重新產生全文,那份內容要接得住;但使用者已經在改的字不能被輪詢回來的值蓋掉,
  // 故只在「本地仍等於上次同步的值」時採用。
  useEffect(() => {
    const next = text || ''
    if (next === syncedPlainRef.current) return
    // 先把上次同步的值抓下來:updater 是延後執行的,先改 ref 的話比對永遠不成立
    const previous = syncedPlainRef.current
    syncedPlainRef.current = next
    setPlain((current) => (current === previous ? next : current))
  }, [text])

  const dirty = plain !== (text || '')

  useNavGuard(dirty, '草稿有未儲存的修改，離開後將遺失。確定要離開？')

  const sections = useMemo(() => splitDraft(plain), [plain])

  function updateSection(key, body) {
    setPlain(joinDraft(sections.map((s) => (s.key === key ? { ...s, body } : s))))
  }

  async function handleSave() {
    setState('saving')
    setMessage('')
    try {
      await updateDraftText(caseId, { text: plain, base_version: versionCount })
      setState('saved')
      setMessage('已儲存')
      onSaved?.()
    } catch (err) {
      setState('error')
      setMessage(err.message || '儲存失敗，請重試。')
      // 版本衝突:同步真實狀態,但不沖掉使用者剛打的字(他還要拿來比對差異)
      if (err.status === 409) onSaved?.()
    }
  }

  async function handleDownload(format) {
    try {
      // 先存再下載:下載到的必須是畫面上這一份,不是上次存的那一份
      if (dirty) await updateDraftText(caseId, { text: plain, base_version: versionCount })
      await (format === 'docx' ? downloadDraftDocx(caseId) : downloadDraftPdf(caseId))
    } catch (err) {
      setState('error')
      setMessage(err.message || '下載失敗，請重試。')
      if (err.status === 409) onSaved?.()
    }
  }

  async function handleFinalize() {
    setState('saving')
    setMessage('')
    try {
      if (dirty) await updateDraftText(caseId, { text: plain, base_version: versionCount })
      const result = await finalizeCase(caseId)
      setState('saved')
      setMessage(`已標記定稿（${result.pdf_location || '已落地'}）`)
      onSaved?.()
    } catch (err) {
      setState('error')
      setMessage(err.message || '定稿失敗，請重試。')
      if (err.status === 409) onSaved?.()
    }
  }

  const isWhole = sections.length === 1 && sections[0].key === 'whole'

  return (
    <div className="draft-workspace" hidden={hidden}>
      <div className="draft-paper" role="group" aria-label="決定書稿紙">
        <p className="doc-intro">
          這一份就是決定書本身：系統依案件資訊與檢索結果先擬好，承辦人直接在這裡改，下載的 PDF 與 Word 印的都是它。
        </p>
        {isWhole ? (
          <AutoTextarea
            id="draft-plain"
            aria-label="決定書全文"
            className="textarea--document"
            value={plain}
            onChange={setPlain}
            hidden={hidden}
          />
        ) : (
          sections.map((s) => (
            <div className={`draft-section draft-section--${s.key}`} key={s.key}>
              {s.title && (
                <label htmlFor={`draft-${s.key}`} className="decision__heading">
                  {s.title}
                </label>
              )}
              <AutoTextarea
                id={`draft-${s.key}`}
                aria-label={SECTION_ARIA[s.key]}
                className="textarea--document draft-section__text"
                value={s.body}
                onChange={(v) => updateSection(s.key, v)}
                hidden={hidden}
              />
            </div>
          ))
        )}
        {draft.cited_laws?.length > 0 && (
          <div className="draft-field">
            <span className="draft-paper__section-title">引用法條</span>
            <p className="mono">{draft.cited_laws.join('、')}</p>
          </div>
        )}
        <div className="action-row draft-actions">
          <span className="draft-actions__meta">
            已存 {versionCount} 版{versionsTruncated ? '（最舊版本已捨棄）' : ''}
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
          <div className="draft-actions__buttons">
            <button
              type="button"
              className="btn btn-secondary"
              onClick={handleSave}
              disabled={state === 'saving' || !dirty}
            >
              {state === 'saving' ? '儲存中…' : '儲存修改'}
            </button>
            <button type="button" className="btn btn-secondary" onClick={() => handleDownload('docx')}>
              下載 Word
            </button>
            {/* 定稿只是標記,不鎖:定稿後仍可修改,改了再存一版 */}
            <button type="button" className="btn btn-secondary" onClick={handleFinalize}>
              {finalizedAt ? '重新定稿' : '標記定稿'}
            </button>
            <button type="button" className="btn btn-primary btn-submit" onClick={() => handleDownload('pdf')}>
              下載 PDF 寄審
            </button>
          </div>
        </div>
      </div>
      <BasisPanel
        laws={laws}
        refs={refs}
        cases={cases}
        track={track}
        onViewSource={onViewSource}
      />
    </div>
  )
}
