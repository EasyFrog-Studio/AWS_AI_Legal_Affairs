import { describe, it, expect } from 'vitest'
import { splitDraft, joinDraft } from '../src/pages/draftSections.js'

const admissibleFull = [
  '新北市政府訴願決定書',
  '',
  '案　　號：113年度訴願字第001號',
  '　訴願人　王○明',
  '　原處分機關　新北市政府環境保護局',
  '',
  '上列訴願人因違反廢棄物清理法事件，不服原處分機關113年5月1日北環稽字第1130001號裁處書，提起訴願，本府決定如下：',
  '',
  '主　文',
  '訴願駁回。',
  '',
  '事　實',
  '訴願人於113年5月1日經稽查違反廢棄物清理法規定，原處分機關依法裁處罰鍰。',
  '',
  '理　由',
  '按訴願法第79條第1項規定，訴願無理由者，應以決定駁回之。本件原處分認事用法並無違誤，應予駁回。',
  '',
  '訴願審議委員會主任委員　林○○',
  '委員　陳○○',
  '委員　黃○○',
  '',
  '如不服本決定，得於決定書送達之次日起二個月內向臺北高等行政法院提起行政訴訟。',
  '',
  '中華民國113年8月1日',
].join('\n')

const inadmissibleNoFact = [
  '新北市政府訴願決定書',
  '',
  '案　　號：113年度訴願字第002號',
  '　訴願人　陳○華',
  '　原處分機關　新北市政府交通事件裁決處',
  '',
  '上列訴願人因交通裁罰事件，不服原處分機關之裁決，提起訴願，因程序不合，本府決定如下：',
  '',
  '主　文',
  '訴願不受理。',
  '',
  '理　由',
  '訴願人提起本件訴願，已逾訴願法第14條第1項所定30日之法定期間，依同法第77條第2款規定，應為不受理之決定。',
  '',
  '訴願審議委員會主任委員　林○○',
  '委員　陳○○',
  '委員　黃○○',
  '',
  '中華民國113年9月1日',
].join('\n')

const noHeadings = '這是一段沒有任何標題的純文字，可能是舊資料或例外格式。'

const missingChairLine = [
  '新北市政府訴願決定書',
  '',
  '主　文',
  '訴願駁回。',
  '',
  '理　由',
  '本件理由略。',
].join('\n')

const missingReasonHeading = [
  '新北市政府訴願決定書',
  '',
  '主　文',
  '訴願駁回。',
].join('\n')

describe('splitDraft / joinDraft', () => {
  it('案 A:完整受理全文（含事實）切成 head/main/fact/reason/tail 五段，還原逐字相等', () => {
    const sections = splitDraft(admissibleFull)
    expect(sections.map((s) => s.key)).toEqual(['head', 'main', 'fact', 'reason', 'tail'])
    // 各段 body 保留了緊接下一個標題前的那一行空行(切分邊界的既有行為)
    const main = sections.find((s) => s.key === 'main')
    expect(main.body).toBe('訴願駁回。\n')
    const fact = sections.find((s) => s.key === 'fact')
    expect(fact.body).toBe('訴願人於113年5月1日經稽查違反廢棄物清理法規定，原處分機關依法裁處罰鍰。\n')
    const reason = sections.find((s) => s.key === 'reason')
    expect(reason.body).toBe(
      '按訴願法第79條第1項規定，訴願無理由者，應以決定駁回之。本件原處分認事用法並無違誤，應予駁回。\n',
    )
    expect(joinDraft(sections)).toBe(admissibleFull)
  })

  it('案 B:不受理全文（無「事　實」）切成四段，還原逐字相等', () => {
    const sections = splitDraft(inadmissibleNoFact)
    expect(sections.map((s) => s.key)).toEqual(['head', 'main', 'reason', 'tail'])
    expect(joinDraft(sections)).toBe(inadmissibleNoFact)
  })

  it('案 C:沒有任何標題的文字退回單一區塊，還原逐字相等', () => {
    const sections = splitDraft(noHeadings)
    expect(sections).toEqual([{ key: 'whole', body: noHeadings }])
    expect(joinDraft(sections)).toBe(noHeadings)
  })

  it('案 D:有標題但缺「訴願審議委員會主任委員」行，tail 為空字串，還原逐字相等', () => {
    const sections = splitDraft(missingChairLine)
    const tail = sections.find((s) => s.key === 'tail')
    expect(tail.body).toBe('')
    expect(joinDraft(sections)).toBe(missingChairLine)
  })

  it('案 E:「主　文」出現但「理　由」缺，退回單一區塊', () => {
    const sections = splitDraft(missingReasonHeading)
    expect(sections).toEqual([{ key: 'whole', body: missingReasonHeading }])
    expect(joinDraft(sections)).toBe(missingReasonHeading)
  })
})
