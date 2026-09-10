import { useCallback, useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import {
  analyzeCase,
  getCase,
  getSource,
  overrideScreening,
  reanalyzeCase,
  replaceDocument,
} from '../api'
import AppShell from '../components/AppShell.jsx'
import DocumentCheckBadge from '../components/DocumentCheckBadge.jsx'
import { DOCUMENT_SLOTS } from '../components/documentSlots.js'
import Icon from '../components/Icon.jsx'
import Seal, { resolveCaseSeal } from '../components/Seal.jsx'
import SourceOverlay from '../components/SourceOverlay.jsx'
import DraftWorkspace from './DraftWorkspace.jsx'
import './CaseDetail.css'

const STAGES = [
  { key: 'f1', label: 'F1 擷取' },
  { key: 'screening', label: '程序審查' },
  { key: 'f2', label: 'F2 法規' },
  { key: 'f2_refs', label: 'F2+ 參考見解' },
  { key: 'f3', label: 'F3 案例' },
]

/** 這一階段此刻正在跑。與程序審查那兩處的判準同一個,不另立一套。 */
function isRunning(key, caseData) {
  return caseData.status === 'processing' && key === caseData.current_stage
}

/** collecting → 待確認;processing → 跟隨 current_stage(f4/done 視為 draft);done → draft;error → current_stage。 */
function autoTarget(caseData) {
  if (!caseData) return 'f1'
  if (caseData.status === 'collecting') return 'collecting'
  if (caseData.status === 'done') return 'draft'
  if (caseData.status === 'processing') {
    return caseData.current_stage === 'f4' || caseData.current_stage === 'done'
      ? 'draft'
      : caseData.current_stage
  }
  if (caseData.status === 'error') return caseData.current_stage
  return 'f1'
}

/** screening.matched_clause 解析出「第 N 款」;解析不到回傳空字串(不寫死條款)。 */
function parseClause(matchedClause) {
  if (!matchedClause) return ''
  const m = /第\s*(\d+)\s*款/.exec(matchedClause)
  return m ? `第 ${m[1]} 款` : ''
}

function stageMarker(key, caseData) {
  const hasData = Boolean(caseData[key])
  if (caseData.status === 'error' && key === caseData.current_stage) {
    return { icon: 'square-error', modifier: 'error', note: '中斷' }
  }
  if (key === 'f2' && caseData.track === 'inadmissible') {
    return { icon: 'square-hollow', modifier: 'na', note: '不適用' }
  }
  if (hasData) {
    return { icon: 'fishtail-solid', modifier: 'done', note: null }
  }
  if (caseData.status === 'processing' && key === caseData.current_stage) {
    return { icon: 'fishtail-accent', modifier: 'active', note: '進行中' }
  }
  return { icon: 'fishtail-hollow', modifier: 'pending', note: null }
}

function draftMarker(caseData) {
  const atDraft = caseData.current_stage === 'f4' || caseData.current_stage === 'done'
  if (caseData.status === 'error' && atDraft && !caseData.f4) {
    return { icon: 'square-error', modifier: 'error', note: '中斷' }
  }
  if (caseData.f4) return { icon: 'fishtail-solid', modifier: 'done', note: null }
  if (caseData.status === 'processing' && atDraft) {
    return { icon: 'fishtail-accent', modifier: 'active', note: '進行中' }
  }
  return { icon: 'fishtail-hollow', modifier: 'pending', note: null }
}

/** 待確認階段:三份文件的型態確認結果、單槽重傳、開始分析。案件在 status='collecting' 時渲染,
 * 取代原有的階段內容——分析尚未開始,F1~F4 都還沒有東西可看。 */
function CollectingSection({ caseData, onReplaced, onAnalyzed }) {
  const [replacing, setReplacing] = useState(null) // 目前正在重傳哪一槽(key),null 代表沒有
  const [replaceText, setReplaceText] = useState('')
  const [replaceFile, setReplaceFile] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const documents = caseData.documents || {}
  // 選填槽沒送來就沒有東西可確認,不能讓它永遠擋著開始分析;送來了就照樣要通過型態確認
  const isEmptyOptional = (slot) => slot.optional && !documents[slot.key]?.text?.trim()
  const allMatched = DOCUMENT_SLOTS.every(
    (s) => documents[s.key]?.check?.matched === true || isEmptyOptional(s),
  )

  async function handleReplaceSubmit(slotKey) {
    setBusy(true)
    setError('')
    try {
      const formData = new FormData()
      // 走 PDF 的案子重傳也要能給 PDF,只收貼上文字等於斷了一半的重傳路徑
      if (replaceFile) formData.append('file', replaceFile)
      else formData.append('text', replaceText)
      await replaceDocument(caseData.case_id, slotKey, formData)
      setReplacing(null)
      setReplaceText('')
      setReplaceFile(null)
      onReplaced()
    } catch (err) {
      setError(err.message || '重傳失敗,請重試。')
      // 409 代表案件已離開收案階段(他處已開始分析):畫面停在過期的收案視圖只會讓人重複操作
      if (err.status === 409) onReplaced()
    } finally {
      setBusy(false)
    }
  }

  async function handleAnalyze() {
    setBusy(true)
    setError('')
    try {
      await analyzeCase(caseData.case_id)
      onAnalyzed()
    } catch (err) {
      setError(err.message || '開始分析失敗,請重試。')
      if (err.status === 409) onAnalyzed()
      setBusy(false)
    }
  }

  return (
    <div className="card">
      <p className="newcase__intro">
        {allMatched
          ? '文件皆已確認無誤,可以開始分析。'
          : '有文件無法確認或判斷不符,請重新上傳該份文件。'}
      </p>
      {DOCUMENT_SLOTS.map((slot) => {
        const doc = documents[slot.key]
        const isReplacing = replacing === slot.key
        return (
          <div className="doc-slot doc-slot--review" key={slot.key}>
            <span className="doc-slot__label">{slot.label}</span>
            {isEmptyOptional(slot) ? (
              <span className="doc-check doc-check--na">— 未提供(選填,可事後補上)</span>
            ) : (
              <DocumentCheckBadge check={doc?.check} />
            )}
            {!isReplacing && (
              <button
                type="button"
                className="btn-link"
                onClick={() => {
                  setReplacing(slot.key)
                  setReplaceText('')
                }}
                disabled={busy}
              >
                重新上傳
              </button>
            )}
            {isReplacing && (
              <div className="doc-slot__replace">
                <label htmlFor={`replace-file-${slot.key}`} className="rail-label">
                  重新上傳 PDF
                </label>
                <input
                  id={`replace-file-${slot.key}`}
                  type="file"
                  accept="application/pdf"
                  className="rail-field"
                  onChange={(e) => setReplaceFile(e.target.files?.[0] || null)}
                  disabled={busy}
                />
                <textarea
                  className="textarea"
                  value={replaceText}
                  onChange={(e) => setReplaceText(e.target.value)}
                  placeholder={`或貼上${slot.label}全文`}
                  rows={4}
                  disabled={busy || Boolean(replaceFile)}
                />
                <div className="action-row">
                  <button
                    type="button"
                    className="btn btn-secondary"
                    onClick={() => {
                      setReplacing(null)
                      setReplaceFile(null)
                    }}
                    disabled={busy}
                  >
                    取消
                  </button>
                  <button
                    type="button"
                    className="btn btn-primary"
                    onClick={() => handleReplaceSubmit(slot.key)}
                    disabled={busy || (!replaceText.trim() && !replaceFile)}
                  >
                    送出
                  </button>
                </div>
              </div>
            )}
          </div>
        )
      })}
      <div className="action-row">
        <button
          type="button"
          className="btn btn-primary"
          onClick={handleAnalyze}
          disabled={!allMatched || busy}
        >
          開始分析
        </button>
      </div>
      {error && <div className="form-result form-result--error">{error}</div>}
    </div>
  )
}

// 期間與程序審查複核時最需要看的六欄(取自送達證書/原處分書/訴願書)。
// 抽不到就顯示「—」而不是整欄不畫:少一欄的畫面看起來一樣完整,那正是最難發現的失效。
const F1_DOCUMENT_FIELDS = [
  ['收受或知悉日', 'receipt_date', true],
  ['送達時間', 'service_date', true],
  ['送達方式', 'service_method', false],
  ['罰鍰金額', 'disposition_fine', false],
  ['處分相對人', 'disposition_recipient', false],
]

function F1Section({ info }) {
  return (
    <dl className="f1-grid">
      <div className="f1-field">
        <dt>訴願人</dt>
        <dd>{info.appellant}</dd>
      </div>
      <div className="f1-field">
        <dt>原處分機關</dt>
        <dd>{info.agency}</dd>
      </div>
      <div className="f1-field">
        <dt>原處分日期</dt>
        <dd className="mono">{info.disposition_date}</dd>
      </div>
      <div className="f1-field">
        <dt>原處分字號</dt>
        <dd className="mono">{info.disposition_no}</dd>
      </div>
      <div className="f1-field">
        <dt>案由類別</dt>
        <dd>{info.case_type}</dd>
      </div>
      <div className="f1-field">
        <dt>援引法條</dt>
        <dd>{(info.cited_articles || []).join('、')}</dd>
      </div>
      {F1_DOCUMENT_FIELDS.map(([label, key, mono]) => (
        <div className="f1-field" key={key}>
          <dt>{label}</dt>
          <dd className={mono ? 'mono' : undefined}>{info[key] || '—'}</dd>
        </div>
      ))}
      <div className="f1-field f1-field--wide">
        <dt>教示條款</dt>
        <dd>{info.disposition_notice_clause || '—'}</dd>
      </div>
      <div className="f1-field f1-field--wide">
        <dt>原處分內容</dt>
        <dd>{info.disposition_summary}</dd>
      </div>
      <div className="f1-field f1-field--wide">
        <dt>訴願理由</dt>
        <dd>
          <ul>
            {(info.appeal_reasons || []).map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </dd>
      </div>
      <div className="f1-field f1-field--wide">
        <dt>爭點</dt>
        <dd>
          <ul>
            {(info.issues || []).map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </dd>
      </div>
    </dl>
  )
}

/** 程序審查結論 + 承辦人推翻入口。自動判之後承辦人只做確認,那就必須推翻得動——
 * 否則自動判等於終局判斷。推翻後不自動重跑檢索(會覆蓋已編輯的草稿),另給重跑按鈕。 */
function ScreeningSection({ caseData, onChanged }) {
  const { screening } = caseData
  const [editing, setEditing] = useState(false)
  const [passed, setPassed] = useState(screening.passed)
  const [clause, setClause] = useState(screening.matched_clause || '')
  const [reasoning, setReasoning] = useState(screening.reasoning || '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const kind = screening.passed ? 'pass' : 'reject'
  const text = screening.passed ? '受理' : '不受理'

  async function handleSubmit() {
    setBusy(true)
    setError('')
    try {
      await overrideScreening(caseData.case_id, {
        passed,
        matched_clause: passed ? null : clause || null,
        reasoning,
      })
      setEditing(false)
      onChanged()
    } catch (err) {
      setError(err.message || '推翻失敗,請重試。')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="card">
      <div className="screening-result">
        <Seal kind={kind} size="lg">
          {text}
        </Seal>
        {screening.matched_clause && (
          <span className="mono">適用條款:{screening.matched_clause}</span>
        )}
        {caseData.screening_system && <span className="doc-check">已由承辦人推翻</span>}
      </div>
      <p className="screening-result__reasoning">{screening.reasoning}</p>
      {screening.review_note && (
        <p className="deadline__note">須人工確認:{screening.review_note}</p>
      )}
      {caseData.screening_system && (
        <p className="screening-result__reasoning">
          系統原判:{caseData.screening_system.passed ? '受理' : '不受理'}
          {caseData.screening_system.matched_clause
            ? `(${caseData.screening_system.matched_clause})`
            : ''}
          。{caseData.screening_system.reasoning}
        </p>
      )}
      {!editing && (
        <div className="action-row">
          <button type="button" className="btn btn-secondary" onClick={() => setEditing(true)}>
            推翻此結論
          </button>
        </div>
      )}
      {editing && (
        <div className="doc-slot__replace">
          <div className="case-list-filter-group">
            <label htmlFor="override-passed" className="rail-label">
              推翻後結論
            </label>
            <select
              id="override-passed"
              className="rail-field"
              value={passed ? 'pass' : 'reject'}
              onChange={(e) => setPassed(e.target.value === 'pass')}
            >
              <option value="pass">受理</option>
              <option value="reject">不受理</option>
            </select>
          </div>
          {!passed && (
            <div className="case-list-filter-group">
              <label htmlFor="override-clause" className="rail-label">
                推翻後適用條款
              </label>
              <input
                id="override-clause"
                type="text"
                className="rail-field"
                placeholder="如 77條第2款"
                value={clause}
                onChange={(e) => setClause(e.target.value)}
              />
            </div>
          )}
          <label htmlFor="override-reasoning" className="rail-label">
            推翻理由
          </label>
          <textarea
            id="override-reasoning"
            className="textarea"
            rows={4}
            value={reasoning}
            onChange={(e) => setReasoning(e.target.value)}
          />
          <div className="action-row">
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => setEditing(false)}
              disabled={busy}
            >
              取消
            </button>
            <button
              type="button"
              className="btn btn-primary"
              onClick={handleSubmit}
              disabled={busy || !reasoning.trim()}
            >
              送出推翻
            </button>
          </div>
          {error && <div className="form-result form-result--error">{error}</div>}
        </div>
      )}
    </div>
  )
}

const DEADLINE_DATES = [
  ['送達生效日', 'service_date'],
  ['期間末日', 'due_date'],
  ['機關收文日', 'filed_date'],
]

/** 期間認定:overdue 為 null 是「無從認定」,與「未逾期」是兩件事,不可合併呈現。 */
function DeadlineSection({ deadline }) {
  if (!deadline) return null
  const verdict =
    deadline.overdue === null || deadline.overdue === undefined
      ? { text: '無從認定', modifier: 'unknown' }
      : deadline.overdue
        ? { text: '已逾期', modifier: 'overdue' }
        : { text: '未逾期', modifier: 'timely' }
  const dates = DEADLINE_DATES.filter(([, key]) => deadline[key])
  return (
    <div className="card deadline">
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
        <p className="deadline__note">須人工確認:{deadline.review_note}</p>
      )}
    </div>
  )
}

/** 沒有結果時,「正在跑」與「這件從沒跑過這一段」必須分得出來(已審結的舊案件屬後者)。 */
function PendingOrNotRun({ running }) {
  return (
    <div className={`state-message state-message--${running ? 'pending' : 'empty'}`}>
      {running ? '檢索中…' : '尚未執行'}
    </div>
  )
}

function F2Section({ laws, track, screening, running, onViewSource }) {
  if (track === 'inadmissible') {
    const clause = parseClause(screening?.matched_clause)
    return (
      <div className="state-message state-message--na">
        {`本案經程序審查認定不受理,依訴願法第 77 條${clause}逕為不受理決定,未進行法規推薦。`}
      </div>
    )
  }
  if (laws === null) {
    return <PendingOrNotRun running={running} />
  }
  if (laws.length === 0) {
    return <div className="state-message state-message--empty">未檢索到相關法規。</div>
  }
  return (
    <div className="card">
      {laws.map((law, i) => (
        <div className="law-ref" key={i}>
          <span className="law-ref__name">
            {law.law_name} 第 {law.article_no} 條
          </span>
          <span className="law-ref__date mono">修正日期 {law.amend_date}</span>
          <p className="law-ref__text">{law.text}</p>
          {law.source_key && (
            <button type="button" className="btn-link" onClick={() => onViewSource(law.source_key)}>
              原文
            </button>
          )}
        </div>
      ))}
    </div>
  )
}

function F2RefsSection({ refs, running, onViewSource }) {
  // 兩條 track 都跑,故沒有「依流程不適用」這一態
  if (refs === null || refs === undefined) {
    return <PendingOrNotRun running={running} />
  }
  if (refs.length === 0) {
    return <div className="state-message state-message--empty">未檢索到相關參考見解。</div>
  }
  return (
    <div className="card">
      {refs.map((ref, i) => (
        <div className="reference-ref" key={i}>
          <span className="reference-ref__kind">{ref.doc_kind}</span>
          <span className="reference-ref__name">{ref.name}</span>
          {ref.issuer && <span className="reference-ref__issuer">{ref.issuer}</span>}
          <span className="reference-ref__date mono">{ref.issued_date}</span>
          {ref.topic && <p className="reference-ref__topic">爭點:{ref.topic}</p>}
          <p className="reference-ref__text">{ref.text}</p>
          {ref.source_key && (
            <button type="button" className="btn-link" onClick={() => onViewSource(ref.source_key)}>
              原文
            </button>
          )}
        </div>
      ))}
    </div>
  )
}

function F3Section({ cases, running, onViewSource }) {
  if (cases === null) {
    return <PendingOrNotRun running={running} />
  }
  if (cases.length === 0) {
    return <div className="state-message state-message--empty">未檢索到相似案例。</div>
  }
  return (
    <div className="card">
      {cases.map((c, i) => (
        <div className="similar-case" key={i}>
          <span className="similar-case__title">
            {c.year}年 {c.case_type} — {c.result}
          </span>
          <div className="similar-case__meta mono">
            案號 {c.case_no} · 訴願條款 {c.appeal_article} · 爭點 {c.issue}
          </div>
          <p>{c.summary}</p>
          <p>{c.similarity_note}</p>
          {/* 原文按鈕:草稿頁的參考依據面板有,階段頁沒有的話兩處呈現不一致 */}
          {c.source_key && (
            <button type="button" className="btn-link" onClick={() => onViewSource(c.source_key)}>
              原文
            </button>
          )}
        </div>
      ))}
    </div>
  )
}

/** 選中階段的內容;status==='error' 且該階段正是 current_stage 時,一律顯示錯誤訊息。 */
function stageContent(key, caseData, onViewSource, onDocumentsChanged) {
  if (caseData.status === 'error' && key === caseData.current_stage) {
    return (
      <div className="state-message state-message--error">
        {caseData.error || '審理過程發生錯誤。'}
      </div>
    )
  }
  if (key === 'collecting') {
    return (
      <CollectingSection
        caseData={caseData}
        onReplaced={onDocumentsChanged}
        onAnalyzed={onDocumentsChanged}
      />
    )
  }
  if (key === 'f1') {
    return caseData.f1 ? (
      <F1Section info={caseData.f1} />
    ) : (
      <div className="state-message state-message--pending">
        {caseData.status === 'processing' && key === caseData.current_stage ? '處理中…' : '尚未執行'}
      </div>
    )
  }
  if (key === 'screening') {
    return caseData.screening ? (
      <>
        <ScreeningSection caseData={caseData} onChanged={onDocumentsChanged} />
        <DeadlineSection deadline={caseData.deadline} />
      </>
    ) : (
      <div className="state-message state-message--pending">
        {caseData.status === 'processing' && key === caseData.current_stage ? '處理中…' : '尚未執行'}
      </div>
    )
  }
  if (key === 'f2') {
    return (
      <F2Section
        laws={caseData.f2}
        track={caseData.track}
        screening={caseData.screening}
        running={isRunning(key, caseData)}
        onViewSource={onViewSource}
      />
    )
  }
  if (key === 'f2_refs') {
    return (
      <F2RefsSection
        refs={caseData.f2_refs}
        running={isRunning(key, caseData)}
        onViewSource={onViewSource}
      />
    )
  }
  if (key === 'f3') {
    return (
      <F3Section cases={caseData.f3} running={isRunning(key, caseData)} onViewSource={onViewSource} />
    )
  }
  return null
}

/** 待人工確認的具體原因(與後端 review.needs_review 的來源同一組事實,但這裡要講出是哪一項)。 */
function reviewNotes(caseData) {
  const reasons = []
  Object.entries(caseData.documents || {}).forEach(([slot, doc]) => {
    const label = DOCUMENT_SLOTS.find((s) => s.key === slot)?.label || slot
    if (doc?.check?.matched !== true) reasons.push(`${label}尚未確認無誤`)
    if (doc?.review_note) reasons.push(`${label}:${doc.review_note}`)
  })
  if (caseData.deadline?.review_note) reasons.push(`訴願期間:${caseData.deadline.review_note}`)
  if (caseData.screening?.review_note) reasons.push(`程序審查:${caseData.screening.review_note}`)
  return reasons
}

export default function CaseDetail() {
  const { id } = useParams()
  const [caseData, setCaseData] = useState(null)
  const [error, setError] = useState('')
  const [actionError, setActionError] = useState('')
  const [reanalyzing, setReanalyzing] = useState(false)
  const [overlayContent, setOverlayContent] = useState(null)
  const [selected, setSelected] = useState(null)
  const [seen, setSeen] = useState(() => new Set())
  const manualRef = useRef(false)
  const timerRef = useRef(null)

  const load = useCallback(async () => {
    try {
      const data = await getCase(id)
      setCaseData(data)
      setError('')
    } catch (err) {
      setError(err.message || '案件載入失敗,請重新整理頁面。')
    }
  }, [id])

  useEffect(() => {
    load()
  }, [load])

  useEffect(() => {
    if (caseData && caseData.status === 'processing') {
      timerRef.current = setTimeout(load, 2000)
    }
    return () => clearTimeout(timerRef.current)
  }, [caseData, load])

  function handleSelect(key) {
    manualRef.current = true
    setSelected(key)
  }

  async function handleReanalyze() {
    setReanalyzing(true)
    setActionError('')
    try {
      await reanalyzeCase(id)
      await load()
    } catch (err) {
      setActionError(err.message || '重跑失敗,請重試。')
    } finally {
      setReanalyzing(false)
    }
  }

  async function handleViewSource(key) {
    try {
      const result = await getSource(key)
      setOverlayContent(result)
    } catch (err) {
      setOverlayContent({ text: err.message || '原文讀取失敗。' })
    }
  }

  const effectiveSelected = caseData
    ? manualRef.current
      ? selected
      : autoTarget(caseData)
    : 'f1'
  const reviewReasons = caseData ? reviewNotes(caseData) : []

  useEffect(() => {
    setSeen(new Set())
  }, [id])

  useEffect(() => {
    // caseData 還是上一個案件的時候不能記:那會把新案件沒看過的階段記成已看過
    if (!caseData || caseData.case_id !== id) return
    setSeen((prev) => (prev.has(effectiveSelected) ? prev : new Set(prev).add(effectiveSelected)))
  }, [caseData, effectiveSelected, id])

  /** 這一階段跑出結果了、而且使用者還沒點進去看過。文件確認不算——那是輸入,不是分析結果。 */
  const hasNewResult = (key) => {
    if (!caseData || seen.has(key)) return false
    return Boolean(key === 'draft' ? caseData.f4 : caseData[key])
  }

  const railSlot = caseData && (
    <nav aria-label="審理歷程">
      <div className="rail-section">
        <div className="rail-section__title">審理歷程</div>
        {(() => {
          const current = effectiveSelected === 'collecting'
          const isCollecting = caseData.status === 'collecting'
          const marker = isCollecting
            ? { icon: 'fishtail-accent', modifier: 'active', note: '進行中' }
            : { icon: 'fishtail-solid', modifier: 'done', note: null }
          return (
            <button
              type="button"
              className={`rail-item ${current ? 'rail-item--current' : ''}`}
              aria-current={current ? 'true' : undefined}
              onClick={() => handleSelect('collecting')}
            >
              <span className={`rail-item__marker rail-item__marker--${marker.modifier}`}>
                <Icon name={marker.icon} />
              </span>
              文件確認
              {marker.note && <span className="rail-item__note">{marker.note}</span>}
            </button>
          )
        })()}
        {STAGES.map((stage) => {
          const marker = stageMarker(stage.key, caseData)
          const current = effectiveSelected === stage.key
          return (
            <button
              type="button"
              key={stage.key}
              className={`rail-item ${current ? 'rail-item--current' : ''}`}
              aria-current={current ? 'true' : undefined}
              onClick={() => handleSelect(stage.key)}
            >
              <span className={`rail-item__marker rail-item__marker--${marker.modifier}`}>
                <Icon name={marker.icon} />
              </span>
              {stage.label}
              {hasNewResult(stage.key) && (
                <span className="rail-item__dot" role="img" aria-label="有新結果" />
              )}
              {marker.note && <span className="rail-item__note">{marker.note}</span>}
            </button>
          )
        })}
        <hr className="rail-divider" />
        {(() => {
          const marker = draftMarker(caseData)
          const current = effectiveSelected === 'draft'
          return (
            <button
              type="button"
              className={`rail-item rail-item--emphasis ${current ? 'rail-item--current' : ''}`}
              aria-current={current ? 'true' : undefined}
              onClick={() => handleSelect('draft')}
            >
              <span className={`rail-item__marker rail-item__marker--${marker.modifier}`}>
                <Icon name={marker.icon} />
              </span>
              決定書草稿
              {hasNewResult('draft') && (
                <span className="rail-item__dot" role="img" aria-label="有新結果" />
              )}
              {marker.note && <span className="rail-item__note">{marker.note}</span>}
            </button>
          )
        })()}
      </div>
    </nav>
  )

  return (
    <AppShell railSlot={railSlot}>
      {error && <div className="state-message state-message--error">{error}</div>}
      {!error && !caseData && (
        <div className="state-message state-message--pending">載入中…</div>
      )}
      {!error && caseData && (
        <>
          <div className="page-header">
            <div>
              <h1 className="page-header__title">{caseData.title || '(未命名案件)'}</h1>
            </div>
            <div className="page-header__actions">
              <Seal kind={resolveCaseSeal(caseData).kind} size="lg">
                {resolveCaseSeal(caseData).text}
              </Seal>
              {/* done 與 error 都可重跑:推翻程序審查之後重跑是 done 狀態下的正常操作 */}
              {(caseData.status === 'done' || caseData.status === 'error') && (
                <button
                  type="button"
                  className="btn btn-secondary"
                  onClick={handleReanalyze}
                  disabled={reanalyzing}
                >
                  {reanalyzing ? '重跑中…' : '重跑分析'}
                </button>
              )}
              <span className="page-header__meta">{caseData.case_id}</span>
            </div>
          </div>

          {reviewReasons.length > 0 && (
            <div className="review-banner" role="status">
              此案有事實待人工確認,請勿逕行送出:
              <ul className="review-banner__list">
                {reviewReasons.map((reason, i) => (
                  <li key={i}>{reason}</li>
                ))}
              </ul>
            </div>
          )}
          {actionError && <div className="form-result form-result--error">{actionError}</div>}

          {caseData.status === 'done' && manualRef.current && effectiveSelected !== 'draft' && (
            <div className="state-message state-message--empty">
              審理完成,可前往決定書草稿。
            </div>
          )}

          {(effectiveSelected === 'collecting' || STAGES.some((s) => s.key === effectiveSelected)) &&
            stageContent(effectiveSelected, caseData, handleViewSource, load)}

          {effectiveSelected === 'draft' &&
            !caseData.f4 &&
            (caseData.status === 'error' &&
            (caseData.current_stage === 'f4' || caseData.current_stage === 'done') ? (
              <div className="state-message state-message--error">
                {caseData.error || '審理過程發生錯誤。'}
              </div>
            ) : (
              <div className="state-message state-message--pending">草稿尚未產生。</div>
            ))}

          {caseData.f4 && (
            <DraftWorkspace
              caseId={caseData.case_id}
              draft={caseData.f4}
              laws={caseData.f2}
              cases={caseData.f3}
              track={caseData.track}
              versionCount={caseData.draft_versions?.length ?? 0}
              versionsTruncated={Boolean(caseData.draft_versions_truncated)}
              finalizedAt={caseData.finalized_at}
              onViewSource={handleViewSource}
              onSaved={load}
              hidden={effectiveSelected !== 'draft'}
            />
          )}
        </>
      )}

      <SourceOverlay content={overlayContent} onClose={() => setOverlayContent(null)} />
    </AppShell>
  )
}
