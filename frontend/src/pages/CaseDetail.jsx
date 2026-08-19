import { useCallback, useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { getCase, getSource, updateDraft, downloadDraftPdf } from '../api'
import Seal, { resolveCaseSeal } from '../components/Seal.jsx'
import SourceOverlay from '../components/SourceOverlay.jsx'

const STAGES = [
  { key: 'f1', label: 'F1 擷取' },
  { key: 'screening', label: '程序審查' },
  { key: 'f2', label: 'F2 法規' },
  { key: 'f3', label: 'F3 案例' },
  { key: 'f4', label: 'F4 草稿' },
]

function stageState(stageKey, hasData, currentStage, status) {
  if (hasData) return 'done'
  if (status === 'processing' && stageKey === currentStage) return 'active'
  return 'pending'
}

function F1Card({ info }) {
  return (
    <div className="card">
      <dl>
        <div className="f1-field">
          <dt>
            <strong>訴願人:</strong>
          </dt>
          <dd>{info.appellant}</dd>
        </div>
        <div className="f1-field">
          <dt>
            <strong>原處分機關:</strong>
          </dt>
          <dd>{info.agency}</dd>
        </div>
        <div className="f1-field">
          <dt>
            <strong>原處分日期:</strong>
          </dt>
          <dd className="mono">{info.disposition_date}</dd>
        </div>
        <div className="f1-field">
          <dt>
            <strong>原處分字號:</strong>
          </dt>
          <dd className="mono">{info.disposition_no}</dd>
        </div>
        <div className="f1-field">
          <dt>
            <strong>原處分內容:</strong>
          </dt>
          <dd>{info.disposition_summary}</dd>
        </div>
        <div className="f1-field">
          <dt>
            <strong>案由類別:</strong>
          </dt>
          <dd>{info.case_type}</dd>
        </div>
        <div className="f1-field">
          <dt>
            <strong>訴願理由:</strong>
          </dt>
          <dd>
            <ul>
              {(info.appeal_reasons || []).map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          </dd>
        </div>
        <div className="f1-field">
          <dt>
            <strong>爭點:</strong>
          </dt>
          <dd>
            <ul>
              {(info.issues || []).map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          </dd>
        </div>
        <div className="f1-field">
          <dt>
            <strong>援引法條:</strong>
          </dt>
          <dd>{(info.cited_articles || []).join('、')}</dd>
        </div>
      </dl>
    </div>
  )
}

function ScreeningCard({ screening }) {
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

function F2Card({ laws, onViewSource }) {
  return (
    <div className="card">
      {laws.length === 0 && <div className="state-message">無相關法規。</div>}
      {laws.map((law, i) => (
        <div className="law-ref" key={i}>
          <span className="law-ref__name">
            {law.law_name} 第 {law.article_no} 條
          </span>
          <span className="law-ref__date">修正日期 {law.amend_date}</span>
          <p className="law-ref__text">{law.text}</p>
          {law.source_key && (
            <button
              type="button"
              className="law-ref__source-link"
              onClick={() => onViewSource(law.source_key)}
            >
              原文
            </button>
          )}
        </div>
      ))}
    </div>
  )
}

function F3Card({ cases }) {
  return (
    <div className="card">
      {cases.length === 0 && <div className="state-message">無相似案例。</div>}
      {cases.map((c, i) => (
        <div className="similar-case" key={i}>
          <span className="similar-case__title">
            {c.year}年 {c.case_type} — {c.result}
          </span>
          <div className="similar-case__meta">
            案號 {c.case_no} · 訴願條款 {c.appeal_article} · 爭點 {c.issue}
          </div>
          <p>{c.summary}</p>
          <p>{c.similarity_note}</p>
        </div>
      ))}
    </div>
  )
}

function BasisPanel({ laws, cases, onViewSource }) {
  return (
    <aside className="basis-panel" aria-label="承辦參考依據">
      <div className="basis-panel__group">
        <div className="basis-panel__heading">參考法規(F2)</div>
        {(!laws || laws.length === 0) && <div className="state-message">無</div>}
        {(laws || []).map((law, i) => (
          <div className="basis-item" key={i}>
            <span className="basis-item__title">
              {law.law_name} 第 {law.article_no} 條
            </span>
            <span className="basis-item__meta">修正日期 {law.amend_date}</span>
            {law.source_key && (
              <button
                type="button"
                className="law-ref__source-link"
                onClick={() => onViewSource(law.source_key)}
              >
                原文
              </button>
            )}
          </div>
        ))}
      </div>
      <div className="basis-panel__group">
        <div className="basis-panel__heading">參考案例(F3)</div>
        {(!cases || cases.length === 0) && <div className="state-message">無</div>}
        {(cases || []).map((c, i) => (
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
                className="law-ref__source-link"
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

function F4Workspace({ caseId, draft, laws, cases, onViewSource, onSaved }) {
  const [fact, setFact] = useState(draft.fact || '')
  const [reason, setReason] = useState(draft.reason || '')
  const [mainText, setMainText] = useState(draft.main_text || '')
  const [state, setState] = useState('idle') // idle | saving | saved | error
  const [message, setMessage] = useState('')

  const dirty =
    fact !== (draft.fact || '') ||
    reason !== (draft.reason || '') ||
    mainText !== (draft.main_text || '')

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
    <div className="draft-workspace">
      <div className="draft-paper draft-paper--editable">
        <h3 className="draft-paper__title">{draft.draft_type}</h3>
        <label className="draft-field">
          <span className="draft-paper__section-title">主文</span>
          <textarea value={mainText} onChange={(e) => setMainText(e.target.value)} rows={2} />
        </label>
        <label className="draft-field">
          <span className="draft-paper__section-title">事實</span>
          <textarea value={fact} onChange={(e) => setFact(e.target.value)} rows={8} />
        </label>
        <label className="draft-field">
          <span className="draft-paper__section-title">理由</span>
          <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={10} />
        </label>
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
      <BasisPanel laws={laws} cases={cases} onViewSource={onViewSource} />
    </div>
  )
}

export default function CaseDetail() {
  const { id } = useParams()
  const [caseData, setCaseData] = useState(null)
  const [error, setError] = useState('')
  const [overlayContent, setOverlayContent] = useState(null)
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

  async function handleViewSource(key) {
    try {
      const result = await getSource(key)
      setOverlayContent(result)
    } catch (err) {
      setOverlayContent({ text: err.message || '原文讀取失敗。' })
    }
  }

  if (error) {
    return (
      <div className="content content--wide">
        <div className="state-message state-message--error">{error}</div>
      </div>
    )
  }

  if (!caseData) {
    return (
      <div className="content content--wide">
        <div className="state-message">載入中…</div>
      </div>
    )
  }

  const seal = resolveCaseSeal(caseData)
  const stageData = {
    f1: caseData.f1,
    screening: caseData.screening,
    f2: caseData.f2,
    f3: caseData.f3,
    f4: caseData.f4,
  }

  return (
    <div className="content content--wide">
      <div className="page-header">
        <div>
          <h1 className="page-header__title">{caseData.title || '(未命名案件)'}</h1>
        </div>
        <div className="page-header__actions">
          <Seal kind={seal.kind} size="lg">
            {seal.text}
          </Seal>
          <span className="page-header__meta">{caseData.case_id}</span>
        </div>
      </div>

      {caseData.status === 'error' && (
        <div className="form-result form-result--error">
          {caseData.error || '審理過程發生錯誤。'}
        </div>
      )}

      <div className="stage-timeline">
        {STAGES.map((stage) => {
          const hasData = Boolean(stageData[stage.key])
          const state = stageState(stage.key, hasData, caseData.current_stage, caseData.status)
          return (
            <div className={`stage-node stage-node--${state}`} key={stage.key}>
              <span className="stage-node__marker" />
              <div className="stage-node__label">{stage.label}</div>
              {state === 'done' && stage.key === 'f1' && <F1Card info={caseData.f1} />}
              {state === 'done' && stage.key === 'screening' && (
                <ScreeningCard screening={caseData.screening} />
              )}
              {state === 'done' && stage.key === 'f2' && (
                <F2Card laws={caseData.f2} onViewSource={handleViewSource} />
              )}
              {state === 'done' && stage.key === 'f3' && <F3Card cases={caseData.f3} />}
              {state === 'done' && stage.key === 'f4' && (
                <F4Workspace
                  caseId={caseData.case_id}
                  draft={caseData.f4}
                  laws={caseData.f2}
                  cases={caseData.f3}
                  onViewSource={handleViewSource}
                  onSaved={load}
                />
              )}
            </div>
          )
        })}
      </div>

      <SourceOverlay content={overlayContent} onClose={() => setOverlayContent(null)} />
    </div>
  )
}
