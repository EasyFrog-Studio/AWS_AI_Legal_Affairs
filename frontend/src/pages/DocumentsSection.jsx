import { useState } from 'react'
import { getDocumentFile, reanalyzeCase, replaceDocument } from '../api'
import DocumentCheckBadge from '../components/DocumentCheckBadge.jsx'
import DropZone from '../components/DropZone.jsx'
import { DOCUMENT_SLOTS } from '../components/documentSlots.js'
import Icon from '../components/Icon.jsx'

/** 一槽的確認結論,四態互斥:ok / mismatch / unknown / na。
 * na(選填槽沒送來)必須與 unknown(送來了但看不懂)分開——前者沒有東西可確認,不是判斷失敗。 */
export function verdictOf(slot, doc) {
  if (slot.optional && !doc?.text?.trim()) return 'na'
  if (doc?.check?.matched === true) return 'ok'
  if (doc?.check?.matched === false) return 'mismatch'
  return 'unknown'
}

const VERDICT_ICON = { ok: 'page-filled', mismatch: 'page-arrow', unknown: 'page-arrow' }

/** 這一槽的原始檔名/來源:PDF 有檔名就給預覽鈕(帶金鑰抓 blob 開新分頁,同 openSourceFile 的做法),
 * 文字輸入沒有檔案可預覽。OCR 取字要講出來:模型抽字會編字,而本系統的正確性建立在日期上。 */
function DocSource({ doc, slot, caseId }) {
  const [error, setError] = useState('')
  if (!doc) return null

  async function handlePreview() {
    setError('')
    try {
      const url = await getDocumentFile(caseId, slot.key)
      window.open(url, '_blank', 'noopener')
    } catch (err) {
      setError(err.message || '預覽失敗，請重試。')
    }
  }

  return (
    <span className="doc-verdict__meta">
      {doc.source === 'pdf' && doc.filename ? (
        <button type="button" className="btn-link" aria-label={`預覽${slot.label}`} onClick={handlePreview}>
          {doc.filename}
        </button>
      ) : doc.source === 'pdf' ? (
        'PDF'
      ) : (
        '文字輸入'
      )}
      {doc.ocr && '（掃描件 OCR 取字）'}
      {error && <span className="form-result form-result--error">{error}</span>}
    </span>
  )
}

/** 待確認階段的一張卷證卡:確認結論與重傳共用同一個版位,展開重傳時版面不跳。
 * replace 是這一槽的重傳狀態與四個動作,收成一包傳——它們永遠一起出現。 */
export function ReviewSlotCard({ slot, doc, replace, busy, caseId, caseProcessing }) {
  const verdict = verdictOf(slot, doc)
  // 案件正在分析中:此刻重傳等於在後端讀這份文件的同時把它換掉,只能等分析結束
  const reuploadDisabled = busy || caseProcessing
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
              <DocSource doc={doc} slot={slot} caseId={caseId} />
            </>
          )}
          <button
            type="button"
            className="btn-link doc-verdict__action"
            onClick={replace.onOpen}
            disabled={reuploadDisabled}
          >
            {verdict === 'na' ? '補上傳' : '重新上傳'}
          </button>
          {caseProcessing && <span className="doc-verdict__meta">分析進行中，暫不可重新上傳</span>}
        </div>
      )}
    </div>
  )
}

/** 文件確認頁:四份文件的型態確認結果、原始檔名預覽、單槽重傳、開始分析。
 * 不再是只有 status='collecting' 才看得到——左欄「文件確認」永遠是完成態的輸入紀錄,
 * 任何階段都能點回來核對當初送進來的是什麼。
 * 版面與收案頁同一個卷證工作檯(doc-grid),兩處看到的是同一批槽,不該長成兩種東西。 */
export function DocumentsSection({ caseData, onChanged }) {
  const [replacing, setReplacing] = useState(null) // 目前正在重傳哪一槽(key),null 代表沒有
  const [replaceFile, setReplaceFile] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const documents = caseData.documents || {}
  const caseProcessing = caseData.status === 'processing'
  const anyMismatch = DOCUMENT_SLOTS.some((s) => verdictOf(s, documents[s.key]) === 'mismatch')

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
      onChanged()
    } catch (err) {
      setError(err.message || '重傳失敗，請重試。')
      // 409 代表案件已離開收案階段(他處已開始分析):畫面停在過期的收案視圖只會讓人重複操作
      if (err.status === 409) onChanged()
    } finally {
      setBusy(false)
    }
  }

  async function handleAnalyze() {
    if (busy || caseProcessing) return
    // 已有 F1 結果時,重新分析會蓋掉 F1 起之後的所有人工修改與草稿,先警示;沒有(第一次分析)就直接跑
    if (
      caseData.f1 != null &&
      !window.confirm(
        '將自 F1 起重新分析整份案件，F1 與程序審查的人工修改及決定書草稿都會被覆蓋（草稿會先存一版）。確定要繼續？',
      )
    ) {
      return
    }
    setBusy(true)
    setError('')
    try {
      await reanalyzeCase(caseData.case_id, 'f1')
      onChanged()
    } catch (err) {
      setError(err.message || '開始分析失敗，請重試。')
      if (err.status === 409) onChanged()
    } finally {
      setBusy(false)
    }
  }

  const analyzeDisabled = busy || caseProcessing

  return (
    <div className="card">
      <p className="doc-intro">
        {anyMismatch
          ? '有文件無法確認或判斷不符，建議重新上傳該份文件後再分析。'
          : '文件皆已確認無誤。'}
      </p>

      <div className="doc-grid">
        {DOCUMENT_SLOTS.map((slot) => (
          <ReviewSlotCard
            key={slot.key}
            slot={slot}
            doc={documents[slot.key]}
            busy={busy}
            caseId={caseData.case_id}
            caseProcessing={caseProcessing}
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
          aria-disabled={analyzeDisabled}
        >
          開始分析
        </button>
        {analyzeDisabled && <span className="doc-verdict__meta">分析進行中</span>}
      </div>

      {error && <div className="form-result form-result--error">{error}</div>}
    </div>
  )
}
