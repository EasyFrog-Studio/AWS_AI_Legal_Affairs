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
