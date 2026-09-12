import { describe, it, expect } from 'vitest'
import { formatBlock, parseBlock, INFO_SPEC, FOOTER_SPEC } from '../src/pages/decisionHeaderText.js'

const FULL = {
  case_no: '1141061379',
  gist: '因違反廢棄物清理法事件提起訴願',
  issued_date: '民國114年12月11日',
  issued_no: '新北府訴決字第1142048878號',
  related_laws: '訴願法 第 79 條\n行政罰法 第 18 條',
  appellant: '鄭○芳',
  agent_role: '送達代收人',
  agent_name: '林○○',
  agency: '新北市政府環境保護局',
  preamble: '上列訴願人因違反廢棄物清理法事件，不服原處分機關民國114年9月16日新北環稽字第41-114-090351號所為之處分，提起訴願一案，本府依法決定如下：',
  chairman: '劉○○',
  committee: '王○○\n李○○',
  decided_date: '民國114年12月11日',
}

const EMPTY = {
  case_no: '',
  gist: '',
  issued_date: '',
  issued_no: '',
  related_laws: '',
  appellant: '',
  agent_role: '',
  agent_name: '',
  agency: '',
  preamble: '',
  chairman: '',
  committee: '',
  decided_date: '',
}

// 決定日期在畫面上不重複紀年:欄名就是「中華民國」,存回去由後端 normalize_roc 補回標準寫法
const ROUND_TRIPPED = { ...FULL, decided_date: '114年12月11日' }

function roundTrip(header) {
  return {
    ...parseBlock(formatBlock(header, INFO_SPEC), INFO_SPEC).values,
    ...parseBlock(formatBlock(header, FOOTER_SPEC), FOOTER_SPEC).values,
  }
}

describe('決定書表頭的純文字書寫層', () => {
  it('填滿的表頭攤成文字再解析回來,十二欄逐字不變', () => {
    expect(roundTrip(FULL)).toEqual(ROUND_TRIPPED)
  })

  it('全空的表頭一樣走得完,標籤仍在,值仍是空字串', () => {
    expect(roundTrip(EMPTY)).toEqual(EMPTY)
    expect(formatBlock(EMPTY, INFO_SPEC)).toContain('案　　號：')
    expect(formatBlock(EMPTY, INFO_SPEC)).toContain('代理人')
  })

  it('多行欄位:縮排續行與重複標籤都併回同一欄', () => {
    const text = ['主任委員：劉○○', '委員：王○○', '　　　李○○', '委員：陳○○', '決定日期：'].join('\n')

    expect(parseBlock(text, FOOTER_SPEC).values.committee).toBe('王○○\n李○○\n陳○○')
  })

  it('標籤寫成決定書上的「案　　號」也認得,值裡的冒號不被當成分隔', () => {
    const text = ['案　　號：1141061379', '要　　旨：不服罰鍰處分：請求撤銷'].join('\n')
    const { values, unknown } = parseBlock(text, INFO_SPEC)

    expect(values.case_no).toBe('1141061379')
    expect(values.gist).toBe('不服罰鍰處分：請求撤銷')
    expect(unknown).toEqual([])
  })

  it('代理人那一列的標籤就是身分,姓名清空則身分跟著清空', () => {
    expect(parseBlock('代理人：林○○', INFO_SPEC).values).toMatchObject({
      agent_role: '代理人',
      agent_name: '林○○',
    })
    expect(parseBlock('送達代收人：林○○', INFO_SPEC).values).toMatchObject({
      agent_role: '送達代收人',
      agent_name: '林○○',
    })
    expect(parseBlock('代理人：', INFO_SPEC).values).toMatchObject({
      agent_role: '',
      agent_name: '',
    })
  })

  it('認不出欄名又接不到欄位的行,在沒有敘明句的區塊回報在 unknown', () => {
    const { unknown } = parseBlock(['中華民國　114年1月1日', '這一行沒有欄名'].join('\n'), FOOTER_SPEC)

    expect(unknown).toEqual(['這一行沒有欄名'])
  })

  it('基本資訊裡沒有欄名的行歸敘明句,不是丟掉也不是回報', () => {
    const { values, unknown } = parseBlock(
      ['案號：114', '這一行沒有欄名', '要旨：x'].join('\n'),
      INFO_SPEC,
    )

    expect(values.preamble).toBe('這一行沒有欄名')
    expect(unknown).toEqual([])
  })


  it('抬頭那兩行不帶資料:照樣攤得出來,解析時跳過,不算認不出的行', () => {
    const text = formatBlock(FULL, INFO_SPEC)

    expect(text).toContain('全　　文：')
    expect(text).toContain('新北市政府訴願決定書')
    expect(parseBlock(text, INFO_SPEC).unknown).toEqual([])
  })

  it('敘明句以冒號結尾也不會被誤認成欄名', () => {
    const text = ['案號：114', '上列訴願人因違反建築法事件，本府決定如下：'].join('\n')
    const { values, unknown } = parseBlock(text, INFO_SPEC)

    expect(values.preamble).toBe('上列訴願人因違反建築法事件，本府決定如下：')
    expect(unknown).toEqual([])
  })

  it('全文標籤與抬頭之間空一行,與列印同一個排法', () => {
    const lines = formatBlock(FULL, INFO_SPEC).split('\n')
    const label = lines.indexOf('全　　文：')

    expect(lines[label + 1]).toBe('')
    expect(lines[label + 2]).toBe('新北市政府訴願決定書')
  })

  it('當事人三列縮排,以全形空格分隔,與決定書抬頭同一個樣子', () => {
    const lines = formatBlock(FULL, INFO_SPEC).split('\n')

    expect(lines).toContain('　　訴願人　鄭○芳')
    expect(lines).toContain('　　送達代收人　林○○')
    expect(lines).toContain('　　原處分機關　新北市政府環境保護局')
  })

  it('委員一位一列,每列都帶欄名', () => {
    expect(formatBlock(FULL, FOOTER_SPEC)).toContain('委員　王○○' + String.fromCharCode(10) + '委員　李○○')
  })

  it('主任委員寫全稱或簡稱都認得', () => {
    expect(formatBlock(FULL, FOOTER_SPEC)).toContain('訴願審議委員會主任委員　劉○○')
    expect(parseBlock('訴願審議委員會主任委員　劉○○', FOOTER_SPEC).values.chairman).toBe('劉○○')
    expect(parseBlock('主任委員：劉○○', FOOTER_SPEC).values.chairman).toBe('劉○○')
  })


  it('欄名與值之間退一格,續行對齊到值欄,與列印同一個欄位表', () => {
    const lines = formatBlock(FULL, INFO_SPEC).split('\n')

    expect(lines).toContain('案　　號：　1141061379')
    expect(lines).toContain('相關法條：　訴願法 第 79 條')
    expect(lines).toContain('　　　　　　行政罰法 第 18 條')
  })

  it('多行欄位開著時,打錯的欄名不會被吞成那一欄的值', () => {
    const text = ['委員：王○○', '決定日其：民國114年12月11日'].join('\n')
    const { values, unknown } = parseBlock(text, FOOTER_SPEC)

    expect(values.committee).toBe('王○○')
    expect(values.decided_date).toBe('')
    expect(unknown).toEqual(['決定日其：民國114年12月11日'])
  })

  it('沒有欄名的續行照樣接進多行欄位', () => {
    const text = ['相關法條：訴願法 第 79 條', '行政罰法 第 18 條'].join('\n')

    expect(parseBlock(text, INFO_SPEC).values.related_laws).toBe(
      '訴願法 第 79 條' + '\n' + '行政罰法 第 18 條',
    )
  })


  it('敘明句跟著表頭一起編輯:沒有欄名的整段文字就是它', () => {
    const text = formatBlock(FULL, INFO_SPEC)

    expect(text.split('\n').pop()).toBe(FULL.preamble)
    expect(parseBlock(text, INFO_SPEC).values.preamble).toBe(FULL.preamble)
    expect(parseBlock(text, INFO_SPEC).unknown).toEqual([])
  })

  it('決定日期那一列的欄名就是中華民國,值不重複紀年', () => {
    expect(formatBlock(FULL, FOOTER_SPEC)).toContain('中華民國　114年12月11日')
    expect(parseBlock('中華民國　114年12月11日', FOOTER_SPEC).values.decided_date).toBe('114年12月11日')
  })

  it('打錯標籤不會被當成續行吞掉,照樣回報', () => {
    const { values, unknown } = parseBlock(['案號：114', '案虎：115'].join('\n'), INFO_SPEC)

    expect(values.case_no).toBe('114')
    expect(unknown).toEqual(['案虎：115'])
  })
})
