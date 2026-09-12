import { describe, it, expect } from 'vitest'
import { splitDraft, joinDraft } from '../src/pages/draftSections.js'

const admissibleFull = [
  '主　文',
  '訴願駁回。',
  '',
  '事　實',
  '訴願人於113年5月1日經稽查違反廢棄物清理法規定，原處分機關依法裁處罰鍰。',
  '',
  '理　由',
  '按訴願法第79條第1項規定，訴願無理由者，應以決定駁回之。本件原處分認事用法並無違誤，應予駁回。',
].join('\n')

const inadmissibleNoFact = [
  '主　文',
  '訴願不受理。',
  '',
  '理　由',
  '訴願人提起本件訴願，已逾訴願法第14條第1項所定30日之法定期間，依同法第77條第2款規定，應為不受理之決定。',
].join('\n')

const noHeadings = '這是一段沒有任何標題的純文字，可能是舊資料或例外格式。'

const missingReasonHeading = ['主　文', '訴願駁回。'].join('\n')

const leadingLegacyHeader = [
  '新北市政府訴願決定書',
  '',
  '主　文',
  '訴願駁回。',
  '',
  '理　由',
  '本件理由略。',
].join('\n')

// 本文前後各有一段多餘空行:主文標題後先空一行才進正文,理由結尾也留兩個空行才到檔尾
const extraBlankLines = [
  '主　文',
  '',
  '訴願駁回。',
  '',
  '理　由',
  '本件理由略。',
  '',
  '',
].join('\n')

describe('splitDraft / joinDraft', () => {
  it('案 A:完整受理全文(含事實)切成 main/fact/reason 三段,還原逐字相等', () => {
    const sections = splitDraft(admissibleFull)
    expect(sections.map((s) => s.key)).toEqual(['main', 'fact', 'reason'])
    const main = sections.find((s) => s.key === 'main')
    expect(main.body).toBe('訴願駁回。\n')
    const fact = sections.find((s) => s.key === 'fact')
    expect(fact.body).toBe('訴願人於113年5月1日經稽查違反廢棄物清理法規定，原處分機關依法裁處罰鍰。\n')
    const reason = sections.find((s) => s.key === 'reason')
    expect(reason.body).toBe(
      '按訴願法第79條第1項規定，訴願無理由者，應以決定駁回之。本件原處分認事用法並無違誤，應予駁回。',
    )
    expect(joinDraft(sections)).toBe(admissibleFull)
  })

  it('案 B:不受理全文(無「事　實」)切成兩段,還原逐字相等', () => {
    const sections = splitDraft(inadmissibleNoFact)
    expect(sections.map((s) => s.key)).toEqual(['main', 'reason'])
    expect(joinDraft(sections)).toBe(inadmissibleNoFact)
  })

  it('案 C:沒有任何標題的文字退回單一區塊,還原逐字相等', () => {
    const sections = splitDraft(noHeadings)
    expect(sections).toEqual([{ key: 'whole', body: noHeadings }])
    expect(joinDraft(sections)).toBe(noHeadings)
  })

  it('案 D:「主　文」出現但「理　由」缺,退回單一區塊', () => {
    const sections = splitDraft(missingReasonHeading)
    expect(sections).toEqual([{ key: 'whole', body: missingReasonHeading }])
    expect(joinDraft(sections)).toBe(missingReasonHeading)
  })

  it('案 E:舊資料表頭殘留(「主　文」不在第一行)整段退回單一區塊,不遺失表頭文字', () => {
    const sections = splitDraft(leadingLegacyHeader)
    expect(sections).toEqual([{ key: 'whole', body: leadingLegacyHeader }])
    expect(joinDraft(sections)).toBe(leadingLegacyHeader)
  })

  it('案 F:本文前後有多餘空行仍逐字還原', () => {
    const sections = splitDraft(extraBlankLines)
    expect(sections.map((s) => s.key)).toEqual(['main', 'reason'])
    const main = sections.find((s) => s.key === 'main')
    expect(main.body).toBe('\n訴願駁回。\n')
    const reason = sections.find((s) => s.key === 'reason')
    expect(reason.body).toBe('本件理由略。\n\n')
    expect(joinDraft(sections)).toBe(extraBlankLines)
  })
})
