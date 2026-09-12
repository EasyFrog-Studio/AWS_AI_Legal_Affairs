import { useState } from 'react'
import SourceSiteLink from '../components/SourceSiteLink.jsx'

/** screening.matched_clause 解析出「第 N 款」;解析不到回傳空字串(不寫死條款)。 */
function parseClause(matchedClause) {
  if (!matchedClause) return ''
  const m = /第\s*(\d+)\s*款/.exec(matchedClause)
  return m ? `第 ${m[1]} 款` : ''
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

const REF_TABS = [
  { key: 'f2', label: '推薦法規（F2）' },
  { key: 'f2_refs', label: '參考見解（F2+）' },
]

/**
 * 參考依據頁:法規與參考見解共用一列分頁(與 F1 擷取同一套分頁列),案例另成一組排在下方。
 * F2+ 沒有條號、不進 F4 的可引用清單,與法規分頁切開才不會被當成可引用法條(見 glossary)。
 */
export function RefsStage({ caseData, isRunning, onViewSource }) {
  const [selectedTab, setSelectedTab] = useState(REF_TABS[0].key)

  return (
    <div className="refs-stack">
      <section className="refs-group">
        <div role="tablist" aria-label="法規與參考見解" className="stage-tabs">
          {REF_TABS.map((tab) => (
            <button
              type="button"
              role="tab"
              key={tab.key}
              id={`refs-tab-${tab.key}`}
              aria-selected={selectedTab === tab.key}
              aria-controls={`refs-panel-${tab.key}`}
              className={`stage-tab ${selectedTab === tab.key ? 'stage-tab--current' : ''}`}
              onClick={() => setSelectedTab(tab.key)}
            >
              {tab.label}
            </button>
          ))}
        </div>
        <div role="tabpanel" id={`refs-panel-${selectedTab}`} aria-labelledby={`refs-tab-${selectedTab}`}>
          {selectedTab === 'f2' ? (
            <F2Section
              laws={caseData.f2}
              track={caseData.track}
              screening={caseData.screening}
              running={isRunning('f2', caseData)}
              onViewSource={onViewSource}
            />
          ) : (
            <>
              <p className="refs-group__note">釋字、函釋與法院裁判供論理參考，沒有條號，不列入決定書的引用法條。</p>
              <F2RefsSection
                refs={caseData.f2_refs}
                running={isRunning('f2_refs', caseData)}
                onViewSource={onViewSource}
              />
            </>
          )}
        </div>
      </section>
      <section className="refs-group">
        <h3 className="refs-group__title">相似案例（F3）</h3>
        <F3Section cases={caseData.f3} running={isRunning('f3', caseData)} onViewSource={onViewSource} />
      </section>
    </div>
  )
}
