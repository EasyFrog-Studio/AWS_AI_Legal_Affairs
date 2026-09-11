export default function Seal({ kind = 'accent', size = 'sm', children }) {
  return <span className={`seal seal--${kind} seal--${size}`}>{children}</span>
}

/**
 * 依 Case 物件推導狀態章樣式與文字。
 * status=processing → 審理中(accent)
 * status=error → 處理失敗(error)
 * needs_review=true(已跑完但有事實待人工認定) → 待人工確認(review)
 * status=done, track=inadmissible → 不受理(reject)
 * status=done, track=admissible → draft_type 文字(駁回/部分不受理部分駁回→reject,撤銷另處/原處分撤銷→pass);無 f4 的清單摘要 → 已審結(accent)
 */
export function resolveCaseSeal(caseData) {
  if (!caseData) return { kind: 'accent', text: '審理中' }
  if (caseData.status === 'error') return { kind: 'error', text: '處理失敗' }
  if (caseData.status === 'collecting') return { kind: 'accent', text: '待確認' }
  if (caseData.status === 'processing') return { kind: 'accent', text: '審理中' }
  // 跑完但有事實待人工認定:不能顯示成一般結案案件,否則要複核的與可直接送的長得一模一樣
  if (caseData.needs_review) return { kind: 'review', text: '待人工確認' }
  if (caseData.track === 'inadmissible') return { kind: 'reject', text: '不受理' }
  if (caseData.track === 'admissible') {
    const draftType = caseData.f4?.draft_type
    if (draftType === '駁回' || draftType === '部分不受理部分駁回') return { kind: 'reject', text: draftType }
    if (draftType === '原處分撤銷' || draftType === '撤銷另處') return { kind: 'pass', text: draftType }
    // 清單摘要(GET /api/cases)不含 f4,已審結的受理案件只能顯示「已審結」
    if (!draftType && caseData.status === 'done') return { kind: 'accent', text: '已審結' }
    return { kind: 'accent', text: draftType || '審理中' }
  }
  return { kind: 'accent', text: '審理中' }
}

/**
 * 清單頁「進度」欄:只回四值,不摻決定結果(結果另由 resolveResultSeal 給)。
 * error 或 collecting+documents_failed → 處理失敗;collecting → 待確認;processing → 審理中;done → 已審結。
 */
export function resolveProgressSeal(row) {
  if (row?.status === 'error') return { kind: 'error', text: '處理失敗' }
  if (row?.status === 'collecting') {
    if (row.documents_failed) return { kind: 'error', text: '處理失敗' }
    return { kind: 'accent', text: '待確認' }
  }
  if (row?.status === 'done') return { kind: 'accent', text: '已審結' }
  return { kind: 'accent', text: '審理中' }
}

/**
 * 清單頁「狀況」欄:如實顯示最終決定結果,不參與流程狀態判斷。
 * 優先讀清單摘要的 result,詳情頁形狀(無 result)退回讀 f4.draft_type;都沒有回 null,呼叫端印「—」。
 */
export function resolveResultSeal(row) {
  const value = row?.result ?? row?.f4?.draft_type
  if (!value) return null
  if (value === '不受理' || value === '駁回' || value === '部分不受理部分駁回') return { kind: 'reject', text: value }
  if (value === '撤銷另處' || value === '原處分撤銷') return { kind: 'pass', text: value }
  return { kind: 'accent', text: value }
}
