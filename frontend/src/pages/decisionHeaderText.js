/** 決定書表頭與結尾的純文字書寫層:承辦人改的是一整段文字,存回去的仍是 DecisionHeader 逐欄。
 * 送出前一定要解析回欄位——PDF 與 Word 是逐欄組版的,認不得一整段文字。
 * 欄名與縮排照語料的決定書體例,畫面上看到的就是列印出來的樣子。 */

// 兩種身分共用同一列,標籤本身就是 agent_role 的值
const AGENT_LABELS = ['代理人', '送達代收人']

export const INFO_SPEC = [
  { field: 'case_no', label: '案　　號' },
  { field: 'gist', label: '要　　旨' },
  { field: 'issued_date', label: '發文日期' },
  { field: 'issued_no', label: '發文字號' },
  { field: 'related_laws', label: '相關法條', multiline: true },
  // 這兩行不帶資料:抬頭的案號由列印時靠右排到機關名那一列,畫面上分成兩行
  { fixed: '全　　文：' },
  { blank: true },
  { fixed: '新北市政府訴願決定書' },
  { field: 'appellant', label: '訴願人', indent: true, gap: true },
  { field: 'agent', roles: true, indent: true, gap: true },
  { field: 'agency', label: '原處分機關', indent: true, gap: true },
  // 敘明句沒有欄名:整段文字自己就是一欄,認不出欄名的行也歸它
  { field: 'preamble', trailing: true },
]

export const FOOTER_SPEC = [
  { field: 'chairman', label: '訴願審議委員會主任委員', aliases: ['主任委員'], gap: true },
  { field: 'committee', label: '委員', multiline: true, repeat: true, gap: true },
  // 決定日期那一列的欄名就是決定書上的「中華民國」,值本身的紀年要脫掉,否則印成中華民國民國
  { field: 'decided_date', label: '中華民國', gap: true, stripEra: true },
]

// 標籤允許寫成「案　　號」:決定書本身就是這樣排版的
const SPACES = /[\s　]+/g
// 欄名後面接全形冒號、全形空格,或整列只有欄名(值還沒填);三者的欄名寫法都可帶全形空格
const LABELLED = [/^([^：:]{1,12})[：:](.*)$/, /^([^：:　]{1,12})　+(.*)$/, /^([^：:　]{1,12})()$/]
const INDENT = '　　'
const ERA_PREFIX = /^(?:中華)?民國\s*/

const labelKey = (text) => text.replace(SPACES, '')

const entryLabel = (entry, header) =>
  entry.roles ? header.agent_role || AGENT_LABELS[0] : entry.label

const entryValue = (entry, header) => {
  const raw = String((entry.roles ? header.agent_name : header[entry.field]) ?? '')
  return entry.stripEra ? raw.replace(ERA_PREFIX, '') : raw
}

/** 逐欄攤成「欄名：值」,多行欄位的續行對齊到值的起點。 */
export function formatBlock(header, spec) {
  const lines = []
  for (const entry of spec) {
    if (entry.blank) {
      lines.push('')
      continue
    }
    if (entry.fixed) {
      lines.push(entry.fixed)
      continue
    }
    if (entry.trailing) {
      const value = entryValue(entry, header)
      if (value) lines.push(value)
      continue
    }
    const label = entryLabel(entry, header)
    const indent = entry.indent ? INDENT : ''
    // 欄名與值之間退一格:與列印的欄位表同一個起筆位置
    const separator = entry.gap ? '　' : '：　'
    const [first = '', ...rest] = entryValue(entry, header).split('\n')
    lines.push(`${indent}${label}${separator}${first}`)
    // 委員一位一列,每列都帶欄名:決定書上就是這樣印的
    const continuation = entry.repeat
      ? `${label}${separator}`
      : '　'.repeat(label.length + separator.length)
    for (const line of rest) lines.push(`${indent}${continuation}${line}`)
  }
  return lines.join('\n')
}

/** 解析回欄位。unknown 是認不出欄名、也接不到多行欄位的行——呼叫端必須擋下儲存,
 * 否則承辦人打的那行會被靜靜丟掉。 */
export function parseBlock(text, spec) {
  const values = {}
  const byLabel = new Map()
  const fixed = new Set()
  for (const entry of spec) {
    if (entry.blank) {
      continue
    } else if (entry.fixed) {
      fixed.add(labelKey(entry.fixed))
    } else if (entry.trailing) {
      values[entry.field] = ''
    } else if (entry.roles) {
      values.agent_role = ''
      values.agent_name = ''
      for (const label of AGENT_LABELS) byLabel.set(label, entry)
    } else {
      values[entry.field] = ''
      for (const label of [entry.label, ...(entry.aliases ?? [])]) {
        byLabel.set(labelKey(label), entry)
      }
    }
  }

  const trailing = spec.find((entry) => entry.trailing)
  const unknown = []
  let open = null
  for (const raw of text.split('\n')) {
    const line = raw.trim()
    if (!line) continue
    if (fixed.has(labelKey(line))) {
      open = null
      continue
    }
    const forms = LABELLED.map((re) => re.exec(line))
    const matched = forms.find((m) => m && byLabel.has(labelKey(m[1])))
    const entry = matched ? byLabel.get(labelKey(matched[1])) : undefined
    if (entry) {
      const value = matched[2].trim()
      if (entry.roles) {
        values.agent_name = value
        values.agent_role = value ? labelKey(matched[1]) : ''
      } else if (entry.multiline && values[entry.field]) {
        // 同一個欄名寫了好幾列(委員一位一列)是自然寫法,不是覆寫
        values[entry.field] = `${values[entry.field]}\n${value}`
      } else {
        values[entry.field] = value
      }
      open = entry.multiline ? entry : null
    } else if (open && !forms[0] && !forms[1]) {
      // 只有不帶欄名的行才是續行:打錯的欄名接進多行欄位就變成一位委員,沒人看得出來
      values[open.field] = values[open.field] ? `${values[open.field]}\n${line}` : line
    } else if (trailing && !forms[0] && !forms[1]) {
      values[trailing.field] = values[trailing.field]
        ? `${values[trailing.field]}\n${line}`
        : line
    } else {
      unknown.push(line)
    }
  }
  return { values, unknown }
}
