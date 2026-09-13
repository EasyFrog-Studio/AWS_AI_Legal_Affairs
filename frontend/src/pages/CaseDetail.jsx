import { useCallback, useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { getCase, getSource, openSourceFile, reanalyzeCase } from '../api'
import AppShell from '../components/AppShell.jsx'
import { DOCUMENT_SLOTS } from '../components/documentSlots.js'
import Icon from '../components/Icon.jsx'
import Seal, { resolveCaseSeal } from '../components/Seal.jsx'
import SourceOverlay from '../components/SourceOverlay.jsx'
import { DocumentsSection } from './DocumentsSection.jsx'
import { F1Section } from './F1Section.jsx'
import { ScreeningSection, DeadlineSection } from './ScreeningSection.jsx'
import { RefsStage } from './RefsSections.jsx'
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

/** 中斷時要從哪個起跑點重新執行:f2/f2_refs/f3/f4 合併成同一個「參考依據以後」起跑點。 */
function retryFrom(currentStage) {
  if (currentStage === 'f1') return 'f1'
  if (currentStage === 'screening') return 'screening'
  return 'f2'
}

/** done/collecting → 草稿頁(collecting 是舊資料的殘留狀態,不再是獨立前端態);
 * processing → 跟隨 current_stage(f4/done 視為 draft);error → 停在中斷的階段。 */
function autoTarget(caseData) {
  if (!caseData) return 'f1'
  if (caseData.status === 'collecting' || caseData.status === 'done') return 'draft'
  if (caseData.status === 'processing') {
    if (caseData.current_stage === 'f4' || caseData.current_stage === 'done') return 'draft'
    return atRefStage(caseData) ? 'refs' : caseData.current_stage
  }
  if (caseData.status === 'error') {
    if (caseData.current_stage === 'f4' || caseData.current_stage === 'done') return 'draft'
    return atRefStage(caseData) ? 'refs' : caseData.current_stage
  }
  return 'f1'
}

function stageMarker(key, caseData) {
  // 參考依據是三個後端階段的合併節點:順序 f3→f2→f2_refs,f2_refs 最後落地才算這一組完成,「不適用」只屬於裡面的法規那一組
  const isRefs = key === 'refs'
  const hasData = isRefs
    ? caseData.f2_refs !== null && caseData.f2_refs !== undefined
    : Boolean(caseData[key])
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

/** 中斷階段的錯誤訊息 + 重新執行:不警示,中斷的階段本來就沒有結果可覆蓋。 */
function ErrorRetry({ message, currentStage, onRetry }) {
  return (
    <>
      <div className="state-message state-message--error">{message || '審理過程發生錯誤。'}</div>
      <div className="action-row action-row--end">
        <button
          type="button"
          className="btn btn-secondary"
          onClick={() => onRetry(retryFrom(currentStage))}
        >
          重新執行
        </button>
      </div>
    </>
  )
}

/** 選中階段的內容;status==='error' 且該階段正是 current_stage 時,一律顯示錯誤訊息 + 重新執行。 */
function stageContent(key, caseData, onViewSource, onChanged, onRetry) {
  // 參考依據是三個後端階段的合併節點,錯誤落在其中任一個都算這一節點中斷
  const atThisStage = key === 'refs' ? atRefStage(caseData) : key === caseData.current_stage
  if (caseData.status === 'error' && atThisStage) {
    return (
      <ErrorRetry message={caseData.error} currentStage={caseData.current_stage} onRetry={onRetry} />
    )
  }
  if (key === 'collecting') {
    return <DocumentsSection caseData={caseData} onChanged={onChanged} />
  }
  if (key === 'f1') {
    return caseData.f1 ? (
      <F1Section caseData={caseData} onChanged={onChanged} />
    ) : (
      <div className="state-message state-message--pending">
        {caseData.status === 'processing' && key === caseData.current_stage ? '處理中…' : '尚未執行'}
      </div>
    )
  }
  if (key === 'screening') {
    return caseData.screening ? (
      <div className="doc-grid doc-grid--stack">
        <ScreeningSection caseData={caseData} onChanged={onChanged} />
        <DeadlineSection deadline={caseData.deadline} />
      </div>
    ) : (
      <div className="state-message state-message--pending">
        {caseData.status === 'processing' && key === caseData.current_stage ? '處理中…' : '尚未執行'}
      </div>
    )
  }
  if (key === 'refs') {
    return <RefsStage caseData={caseData} isRunning={isRunning} onViewSource={onViewSource} />
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
  if (caseData.f1_stale) reasons.push('案件資訊已修改，程序審查尚未依修改後的資料重跑')
  if (caseData.screening_stale) reasons.push('程序審查結論已修改，參考依據與草稿尚未重跑')
  return reasons
}

export default function CaseDetail() {
  const { id } = useParams()
  const [caseData, setCaseData] = useState(null)
  const [error, setError] = useState('')
  const [actionError, setActionError] = useState('')
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

  async function handleRetry(from) {
    setActionError('')
    try {
      await reanalyzeCase(id, from)
      await load()
    } catch (err) {
      setActionError(err.message || '重新執行失敗，請重試。')
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
          // 文件確認永遠是已完成的輸入,不再有 collecting 進行中態
          const marker = { icon: 'fishtail-solid', modifier: 'done', note: null }
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
            stageContent(effectiveSelected, caseData, handleViewSource, load, handleRetry)}

          {effectiveSelected === 'draft' &&
            !caseData.f4 &&
            (caseData.status === 'error' &&
            (caseData.current_stage === 'f4' || caseData.current_stage === 'done') ? (
              <ErrorRetry
                message={caseData.error}
                currentStage={caseData.current_stage}
                onRetry={handleRetry}
              />
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
              header={caseData.decision_header}
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
