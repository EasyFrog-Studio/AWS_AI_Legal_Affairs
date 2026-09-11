/** 決定書全文的分段切分:對應後端 DRAFT_SECTIONS 的段落標題(獨立成行,全形空格)。 */
export const SECTION_TITLES = { main: '主　文', fact: '事　實', reason: '理　由' }

const CHAIR_LINE_PREFIX = '訴願審議委員會主任委員'

/** 切不出「主文」「理由」兩個標題(標題被刪、格式不明)時退回單一區塊,不遺失原文。
 * 「事實」標題可以不存在(不受理決定書事實欄不記載),此時不產生 fact 區塊。 */
export function splitDraft(text) {
  const whole = text || ''
  const lines = whole.split('\n')
  const mainIdx = lines.indexOf(SECTION_TITLES.main)
  const reasonIdx = mainIdx === -1 ? -1 : lines.indexOf(SECTION_TITLES.reason, mainIdx + 1)
  if (mainIdx === -1 || reasonIdx === -1) {
    return [{ key: 'whole', body: whole }]
  }
  const factIdx = lines.indexOf(SECTION_TITLES.fact, mainIdx + 1)
  const hasFact = factIdx !== -1 && factIdx < reasonIdx
  const mainBodyEnd = hasFact ? factIdx : reasonIdx

  let tailStart = lines.findIndex((line) => line.startsWith(CHAIR_LINE_PREFIX), reasonIdx + 1)
  if (tailStart === -1) tailStart = lines.length

  const sections = [
    { key: 'head', body: lines.slice(0, mainIdx).join('\n') },
    { key: 'main', title: SECTION_TITLES.main, body: lines.slice(mainIdx + 1, mainBodyEnd).join('\n') },
  ]
  if (hasFact) {
    sections.push({ key: 'fact', title: SECTION_TITLES.fact, body: lines.slice(factIdx + 1, reasonIdx).join('\n') })
  }
  sections.push({ key: 'reason', title: SECTION_TITLES.reason, body: lines.slice(reasonIdx + 1, tailStart).join('\n') })
  sections.push({ key: 'tail', body: lines.slice(tailStart).join('\n') })
  return sections
}

/** splitDraft 的反函式:對任一 splitDraft 的回傳值,joinDraft(splitDraft(text)) === text。 */
export function joinDraft(sections) {
  if (sections.length === 1 && sections[0].key === 'whole') return sections[0].body
  const pieces = sections.map((s) => (s.title ? `${s.title}\n${s.body}` : s.body))
  // head/tail 可能是零行(如訴願審議委員會那行整段缺漏):空字串在頭尾不占一行,保留在中間才代表原文真的有空行
  return pieces.filter((p, i) => p !== '' || (i !== 0 && i !== pieces.length - 1)).join('\n')
}
