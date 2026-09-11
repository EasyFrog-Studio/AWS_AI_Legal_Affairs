/** 文件型態確認結果:matched=null 是「無法確認」,必須跟 true/false 分開呈現,不可靜默當作通過。
 * NewCase(上傳後首次確認)與 CaseDetail(待確認階段重新檢視)共用同一份呈現規則。 */
export default function DocumentCheckBadge({ check }) {
  if (!check) return null
  if (check.matched === true) {
    return (
      <span className="doc-check doc-check--ok">
        ✓ 確認為此文件（{check.method === 'gemini' ? 'AI 判斷' : '規則判斷'}）
      </span>
    )
  }
  if (check.matched === false) {
    return <span className="doc-check doc-check--mismatch">✕ {check.note}</span>
  }
  return <span className="doc-check doc-check--unknown">? 無法自動確認，請人工核對：{check.note}</span>
}
