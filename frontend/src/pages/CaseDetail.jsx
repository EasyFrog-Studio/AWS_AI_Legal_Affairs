import { useCallback, useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import {
  analyzeCase,
  getCase,
  getSource,
  openSourceFile,
  overrideScreening,
  reanalyzeCase,
  replaceDocument,
  updateCaseInfo,
} from '../api'
import AppShell from '../components/AppShell.jsx'
import DocumentCheckBadge from '../components/DocumentCheckBadge.jsx'
import AutoTextarea from '../components/AutoTextarea.jsx'
import DropZone from '../components/DropZone.jsx'
import { DOCUMENT_SLOTS, F1_GROUPS } from '../components/documentSlots.js'
import Icon from '../components/Icon.jsx'
import Seal, { resolveCaseSeal } from '../components/Seal.jsx'
import SourceOverlay from '../components/SourceOverlay.jsx'
import SourceSiteLink from '../components/SourceSiteLink.jsx'
import DraftWorkspace from './DraftWorkspace.jsx'
import './CaseDetail.css'

const STAGES = [
  { key: 'f1', label: 'F1 擷取' },
  { key: 'screening', label: '程序審查' },
  { key: 'refs', label: '參考依據' },
]
// 左欄的第一項與最後一項各有自己的渲染邏輯,不進 STAGES,但名字要與頁首標題同一份
const COLLECTING_STAGE = { key: 'collecting', label: '文件確認' }
const DRAFT_STAGE = { key: 'draft', label: '決定書草稿' }

/** 頁首標題就是左欄選中項的名字;落在三者之外(error 停在 f4/done)時退回節名。 */
function sectionTitle(selected) {
  const stage = [COLLECTING_STAGE, ...STAGES, DRAFT_STAGE].find((s) => s.key === selected)
  return stage ? stage.label : '審理歷程'
}

/** 合併在「參考依據」底下的三個後端階段。後端仍逐階段跑,左欄只呈現一個節點。 */
const REF_STAGES = ['f2', 'f2_refs', 'f3']
const atRefStage = (caseData) => REF_STAGES.includes(caseData.current_stage)

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
    if (caseData.current_stage === 'f4' || caseData.current_stage === 'done') return 'draft'
    return atRefStage(caseData) ? 'refs' : caseData.current_stage
  }
  if (caseData.status === 'error') return atRefStage(caseData) ? 'refs' : caseData.current_stage
  return 'f1'
}

/** screening.matched_clause 解析出「第 N 款」;解析不到回傳空字串(不寫死條款)。 */
function parseClause(matchedClause) {
  if (!matchedClause) return ''
  const m = /第\s*(\d+)\s*款/.exec(matchedClause)
  return m ? `第 ${m[1]} 款` : ''
}

function stageMarker(key, caseData) {
  // 參考依據是三個後端階段的合併節點:F3 跑完才算這一組完成,「不適用」只屬於裡面的法規那一組
  const isRefs = key === 'refs'
  const hasData = isRefs ? caseData.f3 !== null && caseData.f3 !== undefined : Boolean(caseData[key])
  const atThisStage = isRefs ? atRefStage(caseData) : key === caseData.current_stage
  if (caseData.status === 'error' && atThisStage) {
    return { icon: 'square-error', modifier: 'error', note: '中斷' }
  }
  if (hasData) {
    return { icon: 'fishtail-solid', modifier: 'done', note: null }
  }
  if (caseData.status === 'processing' && atThisStage) {
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

/** 一槽的確認結論,四態互斥:ok / mismatch / unknown / na。
 * na(選填槽沒送來)必須與 unknown(送來了但看不懂)分開——前者沒有東西可確認,不是判斷失敗。 */
function verdictOf(slot, doc) {
  if (slot.optional && !doc?.text?.trim()) return 'na'
  if (doc?.check?.matched === true) return 'ok'
  if (doc?.check?.matched === false) return 'mismatch'
  return 'unknown'
}

const VERDICT_ICON = { ok: 'page-filled', mismatch: 'page-arrow', unknown: 'page-arrow' }

/** 這一槽的文字是怎麼進來的:上傳的寫原始檔名,貼的寫貼上文字。
 * OCR 取字要講出來:模型抽字會編字,而本系統的正確性建立在日期上。 */
function sourceNote(doc) {
  if (!doc) return ''
  const from = doc.source === 'pdf' ? doc.filename || 'PDF' : '貼上文字'
  return doc.ocr ? `來源：${from}（掃描件 OCR 取字）` : `來源：${from}`
}

/** 待確認階段的一張卷證卡:確認結論與重傳共用同一個版位,展開重傳時版面不跳。
 * replace 是這一槽的重傳狀態與四個動作,收成一包傳——它們永遠一起出現。 */
function ReviewSlotCard({ slot, doc, replace, busy }) {
  const verdict = verdictOf(slot, doc)
  return (
    <div className="doc-slot doc-slot--review">
      <div className="doc-slot__head">
        <span className="doc-slot__label">{slot.label}</span>
        <span className="doc-slot__flag">{slot.optional ? '選填' : '必填'}</span>
      </div>
      {replace.active ? (
        <div className="doc-replace">
          {/* 結論留在原地:不符的理由正是他要照著補件的東西,不能在他動手時收走 */}
          <p className="doc-replace__title">
            {verdict === 'na' ? '尚未提供，補上傳' : <DocumentCheckBadge check={doc?.check} />}
          </p>
          <DropZone
            slot={slot}
            fieldId={`replace-file-${slot.key}`}
            file={replace.file}
            onFile={replace.onFile}
            disabled={busy}
          />
          <div className="doc-replace__actions">
            <button type="button" className="btn btn-secondary" onClick={replace.onClose} disabled={busy}>
              取消
            </button>
            <button
              type="button"
              className="btn btn-primary"
              onClick={replace.onSubmit}
              disabled={busy || !replace.file}
            >
              送出
            </button>
          </div>
        </div>
      ) : (
        <div className={`doc-verdict doc-verdict--${verdict}`}>
          {verdict === 'na' ? (
            <span className="doc-check doc-check--na">— 未提供（選填，可事後補上）</span>
          ) : (
            <>
              <Icon name={VERDICT_ICON[verdict]} className="doc-verdict__icon" />
              <DocumentCheckBadge check={doc?.check} />
              <span className="doc-verdict__meta">{sourceNote(doc)}</span>
            </>
          )}
          <button type="button" className="btn-link doc-verdict__action" onClick={replace.onOpen} disabled={busy}>
            {verdict === 'na' ? '補上傳' : '重新上傳'}
          </button>
        </div>
      )}
    </div>
  )
}

/** 待確認階段:四份文件的型態確認結果、單槽重傳、開始分析。案件在 status='collecting' 時渲染,
 * 取代原有的階段內容——分析尚未開始,F1~F4 都還沒有東西可看。
 * 版面與收案頁同一個卷證工作檯(doc-grid),兩處看到的是同一批槽,不該長成兩種東西。 */
function CollectingSection({ caseData, onReplaced, onAnalyzed }) {
  const [replacing, setReplacing] = useState(null) // 目前正在重傳哪一槽(key),null 代表沒有
  const [replaceFile, setReplaceFile] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const documents = caseData.documents || {}
  // 選填槽沒送來就沒有東西可確認,不能讓它永遠擋著開始分析;送來了就照樣要通過型態確認
  const allMatched = DOCUMENT_SLOTS.every((s) => ['ok', 'na'].includes(verdictOf(s, documents[s.key])))

  // 重傳狀態是全體共用的,換槽不清掉的話會把上一槽挑好的檔案帶過去
  function closeReplace() {
    setReplacing(null)
    setReplaceFile(null)
  }

  async function handleReplaceSubmit(slotKey) {
    setBusy(true)
    setError('')
    try {
      const formData = new FormData()
      formData.append('file', replaceFile)
      await replaceDocument(caseData.case_id, slotKey, formData)
      closeReplace()
      onReplaced()
    } catch (err) {
      setError(err.message || '重傳失敗，請重試。')
      // 409 代表案件已離開收案階段(他處已開始分析):畫面停在過期的收案視圖只會讓人重複操作
      if (err.status === 409) onReplaced()
    } finally {
      setBusy(false)
    }
  }

  async function handleAnalyze() {
    if (!allMatched || busy) return
    setBusy(true)
    setError('')
    try {
      await analyzeCase(caseData.case_id)
      onAnalyzed()
    } catch (err) {
      setError(err.message || '開始分析失敗，請重試。')
      if (err.status === 409) onAnalyzed()
      setBusy(false)
    }
  }

  return (
    <div className="card">
      <p className="doc-intro">
        {allMatched
          ? '文件皆已確認無誤，可以開始分析。'
          : '有文件無法確認或判斷不符，請重新上傳該份文件。'}
      </p>

      <div className="doc-grid">
        {DOCUMENT_SLOTS.map((slot) => (
          <ReviewSlotCard
            key={slot.key}
            slot={slot}
            doc={documents[slot.key]}
            busy={busy}
            replace={{
              active: replacing === slot.key,
              file: replaceFile,
              onOpen: () => {
                closeReplace()
                setReplacing(slot.key)
              },
              onClose: closeReplace,
              onFile: setReplaceFile,
              onSubmit: () => handleReplaceSubmit(slot.key),
            }}
          />
        ))}
      </div>

      <div className="action-row action-row--end">
        <button
          type="button"
          className="btn btn-primary btn-submit"
          onClick={handleAnalyze}
          aria-disabled={!allMatched || busy}
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
/** 一欄的顯示值;清單欄位一行一項,空值一律「—」(看得出是沒抽到,不是沒這一欄)。 */
function fieldText(info, field) {
  const value = info[field.key]
  if (field.kind === 'list') return (value || []).join('\n')
  return value || ''
}

/** 卷證總匯表的一欄:唯讀顯示 + 就地編輯。編輯送出的是整份 CaseInfo,只有這一欄換了值。 */
function F1Field({ info, system, field, editable, onSave }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const text = fieldText(info, field)
  // 承辦人改過的欄位要看得出來:改完若與模型抽的長得一樣,下次回頭就分不清哪些字是自己確認過的
  const original = system ? fieldText(system, field) : null
  const edited = original !== null && original !== text

  async function save() {
    setBusy(true)
    setError('')
    try {
      const value =
        field.kind === 'list'
          ? draft.split('\n').map((line) => line.trim()).filter(Boolean)
          : draft.trim()
      await onSave({ ...info, [field.key]: value })
      setEditing(false)
    } catch (err) {
      // 錯誤要看得見,而且不清掉他剛打的字——重打一次是最沒必要的懲罰
      setError(err.message || '儲存失敗，請重試。')
    } finally {
      setBusy(false)
    }
  }

  if (editing) {
    return (
      <div className="f1-field f1-field--editing">
        <dt>
          <label htmlFor={`f1-${field.key}`}>{field.label}</label>
        </dt>
        <dd>
          {field.kind === 'list' ? (
            <AutoTextarea id={`f1-${field.key}`} value={draft} onChange={setDraft} />
          ) : (
            <input
              id={`f1-${field.key}`}
              type="text"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
            />
          )}
          <div className="f1-field__actions">
            <button type="button" className="btn-secondary" onClick={save} disabled={busy}>
              儲存
            </button>
            <button type="button" className="btn-link" onClick={() => setEditing(false)}>
              取消
            </button>
          </div>
          {error && <p className="f1-field__error">{error}</p>}
        </dd>
      </div>
    )
  }

  return (
    <div className={`f1-field ${edited ? 'f1-field--edited' : ''}`}>
      <dt>{field.label}</dt>
      <dd className={field.kind === 'mono' ? 'mono' : undefined}>
        {field.kind === 'list' && text ? (
          <ul>
            {text.split('\n').map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
        ) : (
          text || '—'
        )}
        {edited && (
          <span className="f1-field__original" title={`模型原本擷取：${original || '（空）'}`}>
            {original || '（空）'}
          </span>
        )}
        {editable && (
          <button
            type="button"
            className="btn-link f1-field__edit"
            aria-label={`修改${field.label}`}
            onClick={() => {
              setDraft(text)
              setEditing(true)
            }}
          >
            修改
          </button>
        )}
      </dd>
    </div>
  )
}

/** 這一組底下任一欄被人改過:卡片抬頭標「已修改」,不必逐欄點開才看得出來。 */
function groupEdited(info, system, group) {
  if (!system) return false
  return group.fields.some((field) => fieldText(system, field) !== fieldText(info, field))
}

/** F1 卷證總匯表:依四份文件分組,一組一張卡,眼睛跟著卷宗走。第五組是不出自單一文件的綜合判讀。 */
function F1Section({ info, system, editable, onSave }) {
  return (
    <div className="f1-summary">
      {!editable && (
        <p className="f1-summary__note">分析進行中，此時不開放修改案件資訊。</p>
      )}
      <div className="doc-grid doc-grid--stack">
        {F1_GROUPS.map((group) => (
          <section
            className="doc-slot doc-slot--card"
            key={group.key}
            role="group"
            aria-label={group.label}
          >
            <div className="doc-slot__head">
              <span className="doc-slot__label">{group.label}</span>
              {groupEdited(info, system, group) && <span className="doc-slot__flag">已修改</span>}
            </div>
            <dl className="f1-grid">
              {group.fields.map((field) => (
                <F1Field
                  key={field.key}
                  info={info}
                  system={system}
                  field={field}
                  editable={editable}
                  onSave={onSave}
                />
              ))}
            </dl>
          </section>
        ))}
      </div>
    </div>
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
      setError(err.message || '推翻失敗，請重試。')
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
          <span className="mono">適用條款：{screening.matched_clause}</span>
        )}
        {caseData.screening_system && <span className="doc-check">已由承辦人推翻</span>}
      </div>
      <p className="screening-result__reasoning">{screening.reasoning}</p>
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
      {!editing && (
        <div className="action-row action-row--end">
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
          <AutoTextarea id="override-reasoning" value={reasoning} onChange={setReasoning} />
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
        {`本案經程序審查認定不受理，依訴願法第 77 條${clause}逕為不受理決定，未進行法規推薦。`}
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
    <div className="doc-grid doc-grid--stack">
      {laws.map((law, i) => (
        <div className="doc-slot doc-slot--card law-ref" key={i}>
          <div className="doc-slot__head">
            <span className="law-ref__name">
              {law.law_name} 第 {law.article_no} 條
            </span>
          </div>
          <p className="doc-verdict__meta mono">修正日期 {law.amend_date}</p>
          <p className="ref-card__text">{law.text}</p>
          <div className="ref-card__links">
            {law.source_key && (
              <button type="button" className="btn-link" onClick={() => onViewSource(law.source_key)}>
                原文
              </button>
            )}
            <SourceSiteLink url={law.source_url} />
          </div>
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
    <div className="doc-grid doc-grid--stack">
      {refs.map((ref, i) => (
        <div className="doc-slot doc-slot--card reference-ref" key={i}>
          <div className="doc-slot__head">
            <span className="reference-ref__kind">{ref.doc_kind}</span>
            <span className="reference-ref__name">{ref.name}</span>
          </div>
          <p className="doc-verdict__meta">
            {ref.issuer && <span className="reference-ref__issuer">{ref.issuer}</span>}
            <span className="reference-ref__date mono">{ref.issued_date}</span>
          </p>
          {ref.topic && <p className="reference-ref__topic">爭點：{ref.topic}</p>}
          <p className="ref-card__text">{ref.text}</p>
          <div className="ref-card__links">
            {ref.source_key && (
              <button type="button" className="btn-link" onClick={() => onViewSource(ref.source_key)}>
                原文
              </button>
            )}
            <SourceSiteLink url={ref.source_url} />
          </div>
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
    <div className="doc-grid doc-grid--stack">
      {cases.map((c, i) => (
        <div className="doc-slot doc-slot--card similar-case" key={i}>
          <div className="doc-slot__head">
            <span className="similar-case__title">
              {c.year}年 {c.case_type} — {c.result}
            </span>
          </div>
          <div className="similar-case__meta mono">
            案號 {c.case_no} · 訴願條款 {c.appeal_article} · 爭點 {c.issue}
          </div>
          <p className="ref-card__text">{c.summary}</p>
          {/* 原文按鈕:草稿頁的參考依據面板有,階段頁沒有的話兩處呈現不一致 */}
          <div className="ref-card__links">
            {c.source_key && (
              <button type="button" className="btn-link" onClick={() => onViewSource(c.source_key)}>
                原文
              </button>
            )}
            <SourceSiteLink url={c.source_url} />
          </div>
        </div>
      ))}
    </div>
  )
}

/** 選中階段的內容;status==='error' 且該階段正是 current_stage 時,一律顯示錯誤訊息。 */
function stageContent(key, caseData, onViewSource, onDocumentsChanged) {
  // 參考依據是三個後端階段的合併節點,錯誤落在其中任一個都算這一節點中斷
  const atThisStage = key === 'refs' ? atRefStage(caseData) : key === caseData.current_stage
  if (caseData.status === 'error' && atThisStage) {
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
      <F1Section
        info={caseData.f1}
        system={caseData.f1_system}
        editable={caseData.status !== 'processing'}
        onSave={async (next) => {
          await updateCaseInfo(caseData.case_id, next)
          await onDocumentsChanged()
        }}
      />
    ) : (
      <div className="state-message state-message--pending">
        {caseData.status === 'processing' && key === caseData.current_stage ? '處理中…' : '尚未執行'}
      </div>
    )
  }
  if (key === 'screening') {
    return caseData.screening ? (
      <div className="doc-grid doc-grid--stack">
        <ScreeningSection caseData={caseData} onChanged={onDocumentsChanged} />
        <DeadlineSection deadline={caseData.deadline} />
      </div>
    ) : (
      <div className="state-message state-message--pending">
        {caseData.status === 'processing' && key === caseData.current_stage ? '處理中…' : '尚未執行'}
      </div>
    )
  }
  if (key === 'refs') {
    // 三組同頁但各自標題:F2+ 參考見解沒有條號,湊不出引用格式、不進 F4 的可引用清單,
    // 混成一鍋會讓承辦人把它當成可引用法條(見 glossary)。
    return (
      <div className="refs-stack">
        <section className="refs-group">
          <h3 className="refs-group__title">推薦法規（F2）</h3>
          <F2Section
            laws={caseData.f2}
            track={caseData.track}
            screening={caseData.screening}
            running={isRunning('f2', caseData)}
            onViewSource={onViewSource}
          />
        </section>
        <section className="refs-group">
          <h3 className="refs-group__title">參考見解（F2+）</h3>
          <p className="refs-group__note">釋字、函釋與法院裁判供論理參考，沒有條號，不列入決定書的引用法條。</p>
          <F2RefsSection
            refs={caseData.f2_refs}
            running={isRunning('f2_refs', caseData)}
            onViewSource={onViewSource}
          />
        </section>
        <section className="refs-group">
          <h3 className="refs-group__title">相似案例（F3）</h3>
          <F3Section
            cases={caseData.f3}
            running={isRunning('f3', caseData)}
            onViewSource={onViewSource}
          />
        </section>
      </div>
    )
  }
  return null
}

/** 待人工確認的具體原因(與後端 review.needs_review 的來源同一組事實,但這裡要講出是哪一項)。 */
function reviewNotes(caseData) {
  const reasons = []
  Object.entries(caseData.documents || {}).forEach(([slot, doc]) => {
    const label = DOCUMENT_SLOTS.find((s) => s.key === slot)?.label || slot
    // 空槽沒有東西可確認:答辯書是機關受理後才送來的,收案當下本來就沒有,
    // 算進來的話每一件新案都恆亮。判準與後端 review.needs_review 一致。
    if (doc?.check?.matched !== true && doc?.text?.trim()) reasons.push(`${label}尚未確認無誤`)
    if (doc?.review_note) reasons.push(`${label}：${doc.review_note}`)
  })
  if (caseData.deadline?.review_note) reasons.push(`訴願期間：${caseData.deadline.review_note}`)
  if (caseData.screening?.review_note) reasons.push(`程序審查：${caseData.screening.review_note}`)
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
      setError(err.message || '案件載入失敗，請重新整理頁面。')
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
      setActionError(err.message || '重跑失敗，請重試。')
    } finally {
      setReanalyzing(false)
    }
  }

  async function handleViewSource(key) {
    try {
      const result = await getSource(key)
      // 存檔 PDF 不能當文字塞進 overlay:帶金鑰抓回 blob 再開新分頁
      if (result.file) {
        window.open(await openSourceFile(result.file), '_blank', 'noopener')
        return
      }
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
    if (key === 'draft') return Boolean(caseData.f4)
    // 參考依據:三組任一跑出結果就算有新東西可看
    if (key === 'refs') return REF_STAGES.some((stage) => Boolean(caseData[stage]))
    return Boolean(caseData[key])
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
              onClick={() => handleSelect(COLLECTING_STAGE.key)}
            >
              <span className={`rail-item__marker rail-item__marker--${marker.modifier}`}>
                <Icon name={marker.icon} />
              </span>
              {COLLECTING_STAGE.label}
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
              onClick={() => handleSelect(DRAFT_STAGE.key)}
            >
              <span className={`rail-item__marker rail-item__marker--${marker.modifier}`}>
                <Icon name={marker.icon} />
              </span>
              {DRAFT_STAGE.label}
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
            <div className="page-header__heading">
              <h1 className="page-header__title">
                {sectionTitle(effectiveSelected)}
              </h1>
              <span className="page-header__meta">{caseData.case_id}</span>
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
            </div>
          </div>

          {reviewReasons.length > 0 && (
            <div className="review-banner" role="status">
              此案有事實待人工確認，請勿逕行送出：
              <ul className="review-banner__list">
                {reviewReasons.map((reason, i) => (
                  <li key={i}>{reason}</li>
                ))}
              </ul>
            </div>
          )}
          {actionError && <div className="form-result form-result--error">{actionError}</div>}

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
              text={caseData.draft_plain_text}
              laws={caseData.f2}
              refs={caseData.f2_refs}
              cases={caseData.f3}
              track={caseData.track}
              versionCount={caseData.draft_versions?.length ?? 0}
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
