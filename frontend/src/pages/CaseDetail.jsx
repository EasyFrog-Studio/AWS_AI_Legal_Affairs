import { useCallback, useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { getCase, getSource } from '../api'
import AppShell from '../components/AppShell.jsx'
import Icon from '../components/Icon.jsx'
import Seal, { resolveCaseSeal } from '../components/Seal.jsx'
import SourceOverlay from '../components/SourceOverlay.jsx'
import DraftWorkspace from './DraftWorkspace.jsx'
import './CaseDetail.css'

const STAGES = [
  { key: 'f1', label: 'F1 擷取' },
  { key: 'screening', label: '程序審查' },
  { key: 'f2', label: 'F2 法規' },
  { key: 'f3', label: 'F3 案例' },
]

/** processing → 跟隨 current_stage(f4/done 視為 draft);done → draft;error → current_stage。 */
function autoTarget(caseData) {
  if (!caseData) return 'f1'
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

function ScreeningSection({ screening }) {
  const kind = screening.passed ? 'pass' : 'reject'
  const text = screening.passed ? '受理' : '不受理'
  return (
    <div className="card">
      <div className="screening-result">
        <Seal kind={kind} size="lg">
          {text}
        </Seal>
        {screening.matched_clause && (
          <span className="mono">適用條款:{screening.matched_clause}</span>
        )}
      </div>
      <p className="screening-result__reasoning">{screening.reasoning}</p>
    </div>
  )
}

function F2Section({ laws, track, screening, onViewSource }) {
  if (track === 'inadmissible') {
    const clause = parseClause(screening?.matched_clause)
    return (
      <div className="state-message state-message--na">
        {`本案經程序審查認定不受理,依訴願法第 77 條${clause}逕為不受理決定,未進行法規推薦。`}
      </div>
    )
  }
  if (laws === null) {
    return <div className="state-message state-message--pending">檢索中…</div>
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

function F3Section({ cases }) {
  if (cases === null) {
    return <div className="state-message state-message--pending">檢索中…</div>
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
        </div>
      ))}
    </div>
  )
}

/** 選中階段的內容;status==='error' 且該階段正是 current_stage 時,一律顯示錯誤訊息。 */
function stageContent(key, caseData, onViewSource) {
  if (caseData.status === 'error' && key === caseData.current_stage) {
    return (
      <div className="state-message state-message--error">
        {caseData.error || '審理過程發生錯誤。'}
      </div>
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
      <ScreeningSection screening={caseData.screening} />
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
        onViewSource={onViewSource}
      />
    )
  }
  if (key === 'f3') {
    return <F3Section cases={caseData.f3} />
  }
  return null
}

export default function CaseDetail() {
  const { id } = useParams()
  const [caseData, setCaseData] = useState(null)
  const [error, setError] = useState('')
  const [overlayContent, setOverlayContent] = useState(null)
  const [selected, setSelected] = useState(null)
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

  const railSlot = caseData && (
    <nav aria-label="審理歷程">
      <div className="rail-section">
        <div className="rail-section__title">審理歷程</div>
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
              <span className="page-header__meta">{caseData.case_id}</span>
            </div>
          </div>

          {caseData.status === 'done' && manualRef.current && effectiveSelected !== 'draft' && (
            <div className="state-message state-message--empty">
              審理完成,可前往決定書草稿。
            </div>
          )}

          {STAGES.some((s) => s.key === effectiveSelected) &&
            stageContent(effectiveSelected, caseData, handleViewSource)}

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
