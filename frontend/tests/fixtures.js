// 依 mock 後端 GET /api/cases/:id 回應形狀裁剪
const f1 = {
  appellant: '王○明',
  agency: '新北市政府環境保護局',
  disposition_date: '113-05-01',
  disposition_no: '北環稽字第1130001號',
  disposition_summary: '違反廢棄物清理法裁處罰鍰',
  case_type: '環保',
  appeal_reasons: ['未收到處分書'],
  issues: ['訴願逾期'],
  cited_articles: ['訴願法第14條'],
}
const f2 = [
  {
    law_name: '訴願法',
    article_no: '14',
    text: '訴願之提起,應自行政處分達到或公告期滿之次日起三十日內為之。',
    amend_date: '101-06-27',
    source_key: null,
    relevance: '期間',
  },
  {
    law_name: '訴願法',
    article_no: '77',
    text: '訴願事件有左列各款情形之一者,應為不受理之決定。',
    amend_date: '101-06-27',
    source_key: null,
    relevance: '不受理',
  },
]
const f3 = [
  {
    case_no: '112-0001',
    year: '112',
    case_type: '環保',
    appeal_article: '77II',
    issue: '訴願逾期',
    result: '不受理',
    summary: '摘要一',
    similarity_note: '備註一',
    source_key: null,
    source_url: 'https://web.law.ntpc.gov.tw/Scripts/Su_contents03.aspx?EANO=1120001',
  },
  {
    case_no: '112-0002',
    year: '112',
    case_type: '環保',
    appeal_article: '79I',
    issue: '訴願無理由',
    result: '駁回',
    summary: '摘要二',
    similarity_note: '備註二',
    source_key: null,
    source_url: null,
  },
]

const f2Refs = [
  {
    doc_kind: '司法院釋字',
    name: '釋字第469號',
    issuer: '',
    issued_date: '未收錄',
    topic: '怠於執行職務之國家賠償責任',
    text: '法律規定之內容非僅屬授予國家機關推行公共事務之權限…',
    source_key: null,
    relevance: '向量檢索命中(KB-LAW,非法規)',
  },
  {
    doc_kind: '行政函釋',
    name: '法務部 法律字第0930014628號',
    issuer: '法務部',
    issued_date: '民國 93 年 04 月 13 日',
    topic: '',
    text: '關於寄存送達自寄存之日起經十日發生效力…',
    source_key: 'markdown/行政函釋/法務部93年4月13日法律字0930014628號函-寄存送達.md',
    relevance: '向量檢索命中(KB-LAW,非法規)',
  },
]

export const doneAdmissible = {
  case_id: 'c-1',
  created_at: '2026-08-27T03:00:00+00:00',
  title: '訴願書 A',
  status: 'done',
  current_stage: 'done',
  track: 'admissible',
  source: 'text',
  input_text: '…',
  f1,
  screening: { passed: true, matched_clause: null, reasoning: '形式審查無不受理事由。' },
  deadline: {
    overdue: null,
    service_date: null,
    due_date: null,
    filed_date: null,
    detail: '',
    review_note: '期間未計算,送達日無法認定:抽到 0 個',
  },
  f2,
  f2_refs: f2Refs,
  f3,
  f4: { draft_type: '駁回', fact: '事實原文', reason: '理由原文', main_text: '訴願駁回。' },
  // 決定書全文:F4 產出時攤平寫入,承辦人改的就是它
  draft_plain_text: '新北市政府訴願決定書 主文 訴願駁回。 事實 事實原文 理由 理由原文',
  error: null,
}

export const doneInadmissible = {
  ...doneAdmissible,
  case_id: 'c-2',
  title: '訴願書 B',
  track: 'inadmissible',
  screening: { passed: false, matched_clause: '77條第2款', reasoning: '逾三十日提起。' },
  deadline: {
    overdue: true,
    service_date: '2025-05-28',
    due_date: '2025-06-27',
    filed_date: '2025-10-31',
    detail: '送達生效日114年5月28日,起算日114年5月29日,期間30日、在途0日,末日114年6月27日,機關收文日114年10月31日,已逾期。',
    review_note: '',
  },
  f2: null,
  f2_refs: f2Refs,
  f4: { draft_type: '不受理', fact: '事實原文B', reason: '理由原文B', main_text: '訴願不受理。' },
  draft_plain_text: '新北市政府訴願決定書 主文 訴願不受理。 理由 理由原文B',
}

export const collectingAllMatched = {
  case_id: 'c-5',
  created_at: '2026-08-27T04:00:00+00:00',
  title: '訴願書 D',
  status: 'collecting',
  current_stage: 'f1',
  track: null,
  source: 'text',
  input_text: '…',
  documents: {
    appeal: { slot: 'appeal', source: 'text', text: '訴願書全文', check: { matched: true, method: 'rule', note: '符合訴願書的文字特徵' } },
    service: { slot: 'service', source: 'text', text: '送達證書全文', check: { matched: true, method: 'rule', note: '符合送達證書的文字特徵' } },
    disposition: { slot: 'disposition', source: 'text', text: '原處分書全文', check: { matched: true, method: 'rule', note: '符合原處分書的文字特徵' } },
    // 選填槽,這件收案時機關還沒送答辯書
    answer: { slot: 'answer', source: 'text', text: '', check: { matched: null, method: 'none', note: '文件內容為空,無法確認' } },
  },
  f1: null,
  screening: null,
  deadline: null,
  f2: null,
  f3: null,
  f4: null,
  error: null,
}

export const collectingWithMismatch = {
  ...collectingAllMatched,
  case_id: 'c-6',
  title: '訴願書 E',
  documents: {
    ...collectingAllMatched.documents,
    service: {
      slot: 'service',
      source: 'text',
      text: '送達證書全文',
      check: { matched: false, method: 'rule', note: '文字特徵更接近原處分書,不是送達證書' },
    },
  },
}

export function processingAt(currentStage) {
  const stages = ['f1', 'screening', 'f2', 'f2_refs', 'f3', 'f4']
  const idx = stages.indexOf(currentStage)
  return {
    ...doneAdmissible,
    case_id: 'c-3',
    title: '訴願書 C',
    status: 'processing',
    current_stage: currentStage,
    f1: idx > 0 ? f1 : null,
    screening: idx > 1 ? doneAdmissible.screening : null,
    f2: idx > 2 ? f2 : null,
    // f2_refs 也要照階段給值:從 doneAdmissible 整份帶過來的話,還沒跑到的階段會有結果
    f2_refs: idx > 3 ? f2Refs : null,
    f3: idx > 4 ? f3 : null,
    f4: null,
  }
}

export const errorAtF2 = {
  ...processingAt('f2'),
  case_id: 'c-4',
  status: 'error',
  error: 'Bedrock 檢索逾時。',
}

export const listRows = [
  {
    case_id: 'c-0001',
    created_at: '2026-08-27T01:00:00+00:00',
    title: '環保裁罰逾期',
    status: 'done',
    track: 'inadmissible',
    current_stage: 'done',
    case_type: '環保',
  },
  {
    case_id: 'c-0002',
    created_at: '2026-08-27T02:00:00+00:00',
    title: '交通罰單駁回',
    status: 'done',
    track: 'admissible',
    current_stage: 'done',
    case_type: '交通',
  },
  {
    case_id: 'c-0003',
    created_at: '2026-08-27T03:00:00+00:00',
    title: '建管審理中',
    status: 'processing',
    track: null,
    current_stage: 'f2',
    case_type: null,
  },
]
