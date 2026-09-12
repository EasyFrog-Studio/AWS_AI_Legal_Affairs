/** 決定書本文的分段切分:對應後端 DRAFT_SECTIONS 的段落標題(獨立成行,全形空格)。表頭與結尾另走結構化欄位,不在這份純文字裡。 */
export const SECTION_TITLES = { main: '主　文', fact: '事　實', reason: '理　由' }

/** 切不出「主文」在第一行或「理由」標題(格式不明、舊資料殘留表頭)時退回單一區塊,不遺失原文。
 * 「事實」標題可以不存在(不受理決定書事實欄不記載),此時不產生 fact 區塊。
 * 「理由」之後的所有行(含結尾空行)整段併入 reason,本文不再有 tail。 */
export function splitDraft(text) {
  const whole = text || ''
  const lines = whole.split('\n')
  const mainIdx = lines.indexOf(SECTION_TITLES.main)
  const reasonIdx = mainIdx === -1 ? -1 : lines.indexOf(SECTION_TITLES.reason, mainIdx + 1)
  if (mainIdx !== 0 || reasonIdx === -1) {
    return [{ key: 'whole', body: whole }]
  }
  const factIdx = lines.indexOf(SECTION_TITLES.fact, mainIdx + 1)
  const hasFact = factIdx !== -1 && factIdx < reasonIdx
  const mainBodyEnd = hasFact ? factIdx : reasonIdx

  const sections = [
    { key: 'main', title: SECTION_TITLES.main, body: lines.slice(mainIdx + 1, mainBodyEnd).join('\n') },
  ]
  if (hasFact) {
    sections.push({ key: 'fact', title: SECTION_TITLES.fact, body: lines.slice(factIdx + 1, reasonIdx).join('\n') })
  }
  sections.push({ key: 'reason', title: SECTION_TITLES.reason, body: lines.slice(reasonIdx + 1).join('\n') })
  return sections
}

/** splitDraft 的反函式:對任一 splitDraft 的回傳值,joinDraft(splitDraft(text)) === text。 */
export function joinDraft(sections) {
  if (sections.length === 1 && sections[0].key === 'whole') return sections[0].body
  return sections.map((s) => `${s.title}\n${s.body}`).join('\n')
}
