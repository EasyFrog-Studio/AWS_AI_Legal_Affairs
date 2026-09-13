import { useEffect, useMemo, useRef, useState } from 'react'
import {
  updateDraftText,
  updateDraftResult,
  updateDecisionHeader,
  downloadDraftPdf,
  downloadDraftDocx,
} from '../api'
import AutoTextarea from '../components/AutoTextarea.jsx'
import SourceSiteLink from '../components/SourceSiteLink.jsx'
import { useNavGuard } from '../navGuard.js'
import { formatBlock, parseBlock, INFO_SPEC, FOOTER_SPEC } from './decisionHeaderText.js'
import { REF_KINDS } from './refKinds.js'
import { inadmissibleLawNote } from './f2Notes.js'
import { splitDraft, joinDraft } from './draftSections.js'
import './DraftWorkspace.css'

const SECTION_ARIA = { main: '主文', fact: '事實', reason: '理由' }

// 值域與順序同 models.DraftType,五值全開:改結果是承辦人的權限,不受目前 track 收斂
const DRAFT_RESULT_OPTIONS = ['不受理', '駁回', '撤銷另處', '原處分撤銷', '部分不受理部分駁回']

// 教示條款文字同後端 build_decision_blocks:這兩種決定結果本來就沒有救濟教示
const NOTICE_TEXT =
  '如不服本決定，得於決定書送達之次日起 2 個月內向臺北高等行政法院（地址：臺北市士林區福國路 101 號）提起行政訴訟。'
const NO_NOTICE_RESULTS = ['原處分撤銷', '撤銷另處']

function BasisPanel({ laws, refs, cases, track, screening, onViewSource }) {
  return (
    <aside className="basis-panel" aria-label="承辦參考依據">
      {/* 不受理案這一組照樣列出,只是說明為何沒有推薦——空著會讓人以為檢索失敗 */}
      <div className="basis-panel__group">
        <div className="basis-panel__heading">參考法規（F2）</div>
        {track === 'inadmissible' ? (
          <div className="state-message state-message--na">{inadmissibleLawNote(screening)}</div>
        ) : laws === null ? (
          <div className="state-message state-message--pending">檢索中…</div>
        ) : laws.length === 0 ? (
          <div className="state-message state-message--empty">未檢索到相關法規。</div>
        ) : (
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
          ))
        )}
      </div>
      {/* 參考見解兩條 track 都跑,故沒有 track 條件;三類各占一組,與階段頁的三個分頁同一套分法 */}
      {REF_KINDS.map((kind) => {
        const ofKind = (refs ?? []).filter((ref) => ref.doc_kind === kind.key)
        return (
          <div className="basis-panel__group" key={kind.key}>
            <div className="basis-panel__heading">{kind.key}（F2+）</div>
            {refs === null || refs === undefined ? (
              <div className="state-message state-message--pending">檢索中…</div>
            ) : ofKind.length === 0 ? (
              <div className="state-message state-message--empty">{kind.empty}</div>
            ) : (
              ofKind.map((ref, i) => (
                <div className="basis-item" key={i}>
                  <span className="basis-item__title">{ref.name}</span>
                  <span className="basis-item__meta">
                    {ref.issuer ? `${ref.issuer} · ` : ''}
                    {ref.issued_date}
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
              ))
            )}
          </div>
        )
      })}
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
  screening,
  header = {},
  versionCount = 0,
  onViewSource,
  onSaved,
  hidden,
}) {
  const [plain, setPlain] = useState(text || '')
  const [result, setResult] = useState(draft.draft_type)
  const infoText0 = formatBlock(header, INFO_SPEC)
  const footerText0 = formatBlock(header, FOOTER_SPEC)
  const [infoText, setInfoText] = useState(infoText0)
  const [footerText, setFooterText] = useState(footerText0)
  const [state, setState] = useState('idle') // idle | saving | saved | error
  const [message, setMessage] = useState('')
  const prevCaseIdRef = useRef(caseId)
  const syncedPlainRef = useRef(text || '')
  const syncedResultRef = useRef(draft.draft_type)
  const syncedInfoRef = useRef(infoText0)
  const syncedFooterRef = useRef(footerText0)

  useEffect(() => {
    if (prevCaseIdRef.current !== caseId) {
      prevCaseIdRef.current = caseId
      setPlain(text || '')
      syncedPlainRef.current = text || ''
      setResult(draft.draft_type)
      syncedResultRef.current = draft.draft_type
      setInfoText(infoText0)
      syncedInfoRef.current = infoText0
      setFooterText(footerText0)
      syncedFooterRef.current = footerText0
      setState('idle')
      setMessage('')
    }
  }, [caseId, draft, infoText0, footerText0])

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

  // 重跑會改判決定結果,接法與全文同一套
  useEffect(() => {
    const next = draft.draft_type
    if (next === syncedResultRef.current) return
    const previous = syncedResultRef.current
    syncedResultRef.current = next
    setResult((current) => (current === previous ? next : current))
  }, [draft.draft_type])

  // 表頭與結尾的接法同全文:整段比對,本地已經在改就不被輪詢回來的值蓋掉
  useEffect(() => {
    if (infoText0 === syncedInfoRef.current) return
    const previous = syncedInfoRef.current
    syncedInfoRef.current = infoText0
    setInfoText((current) => (current === previous ? infoText0 : current))
  }, [infoText0])

  useEffect(() => {
    if (footerText0 === syncedFooterRef.current) return
    const previous = syncedFooterRef.current
    syncedFooterRef.current = footerText0
    setFooterText((current) => (current === previous ? footerText0 : current))
  }, [footerText0])

  const textDirty = plain !== (text || '')
  const resultDirty = result !== draft.draft_type
  const headerDirty = infoText !== infoText0 || footerText !== footerText0
  const dirty = textDirty || resultDirty || headerDirty

  useNavGuard(dirty, '草稿有未儲存的修改，離開後將遺失。確定要離開？')

  const sections = useMemo(() => splitDraft(plain), [plain])

  function updateSection(key, body) {
    setPlain(joinDraft(sections.map((s) => (s.key === key ? { ...s, body } : s))))
  }

  async function handleSave() {
    if (state === 'saving') return
    if (!dirty) {
      setState('error')
      setMessage('沒有可儲存的修改')
      return
    }
    const info = parseBlock(infoText, INFO_SPEC)
    const footer = parseBlock(footerText, FOOTER_SPEC)
    const stray = [...info.unknown, ...footer.unknown][0]
    // 認不出標籤的行存不回任何欄位,整份擋下來:讓它送出等於把那一行丟掉
    if (headerDirty && stray) {
      setState('error')
      setMessage(`這一行認不出是哪一欄，請保留「欄名：」的寫法：${stray}`)
      return
    }
    setState('saving')
    setMessage('')
    try {
      // 表頭先送:文件只有一份,表頭與本文分兩支 API,失敗時已成功的一支不回滾
      if (headerDirty) await updateDecisionHeader(caseId, { ...info.values, ...footer.values })
      // 只改結果時不送全文:那會平白多存一版,版本歷史讀起來像改過內容但沒改
      if (textDirty) {
        await updateDraftText(caseId, { text: plain, base_version: versionCount })
      }
      if (resultDirty) await updateDraftResult(caseId, { draft_type: result })
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
    // 下載的是伺服器上那一份,未儲存就下載會拿到跟畫面不同的文件,故先擋下
    if (dirty) {
      setState('error')
      setMessage('尚未儲存修改')
      return
    }
    try {
      await (format === 'docx' ? downloadDraftDocx(caseId) : downloadDraftPdf(caseId))
    } catch (err) {
      setState('error')
      setMessage(err.message || '下載失敗，請重試。')
      if (err.status === 409) onSaved?.()
    }
  }

  const isWhole = sections.length === 1 && sections[0].key === 'whole'

  return (
    <div className="draft-workspace" hidden={hidden}>
      <div className="draft-paper" role="group" aria-label="決定書稿紙">
        <section className="draft-block">
          <span className="draft-paper__section-title">基本資訊</span>
          <AutoTextarea
            id="draft-header"
            aria-label="基本資訊"
            className="textarea--document draft-block__text"
            value={infoText}
            onChange={setInfoText}
            hidden={hidden}
          />
        </section>

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
              <label htmlFor={`draft-${s.key}`} className="decision__heading">
                {s.title}
              </label>
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
        <section className="draft-block">
          <AutoTextarea
            id="draft-footer"
            aria-label="決定書結尾"
            className="textarea--document draft-block__text"
            value={footerText}
            onChange={setFooterText}
            hidden={hidden}
          />
          {!NO_NOTICE_RESULTS.includes(result) && (
            <p className="draft-notice">
              {NOTICE_TEXT}
              <br />
              （由系統依決定結果帶入，不可編輯；列印時排在決定日期之前）
            </p>
          )}
        </section>

        <div className="draft-result">
          <select
            aria-label="結果"
            className="input"
            value={result}
            onChange={(e) => setResult(e.target.value)}
          >
            {DRAFT_RESULT_OPTIONS.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </div>

        <div className="action-row draft-actions">
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
            {/* 未儲存不用 disabled:按鈕要按得下去才說得出「尚未儲存修改」 */}
            <button
              type="button"
              className="btn btn-secondary"
              aria-disabled={dirty}
              onClick={() => handleDownload('docx')}
            >
              下載 Word
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              aria-disabled={dirty}
              onClick={() => handleDownload('pdf')}
            >
              下載 PDF
            </button>
            {/* 與新增案件的送出鍵同一套:aria-disabled 保住主色,原生 disabled 會連游標一起吃掉 */}
            <button
              type="button"
              className={`btn btn-primary btn-submit ${state === 'saving' ? 'btn--loading' : ''}`}
              aria-disabled={state === 'saving' || !dirty}
              onClick={handleSave}
            >
              {state === 'saving' && <span className="btn__spinner" aria-hidden="true" />}
              {state === 'saving' ? '儲存中…' : '儲存修改'}
            </button>
          </div>
        </div>
      </div>
      <BasisPanel
        laws={laws}
        refs={refs}
        cases={cases}
        track={track}
        screening={screening}
        onViewSource={onViewSource}
      />
    </div>
  )
}
