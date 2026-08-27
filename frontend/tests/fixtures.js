// 依 mock 後端 GET /api/cases/:id 實際回應形狀(2026-08-27 抓取)裁剪
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
  f2,
  f3,
  f4: { draft_type: '駁回', fact: '事實原文', reason: '理由原文', main_text: '訴願駁回。' },
  error: null,
}

export const doneInadmissible = {
  ...doneAdmissible,
  case_id: 'c-2',
  title: '訴願書 B',
  track: 'inadmissible',
  screening: { passed: false, matched_clause: '77條第2款', reasoning: '逾三十日提起。' },
  f2: null,
  f4: { draft_type: '不受理', fact: '事實原文B', reason: '理由原文B', main_text: '訴願不受理。' },
}

export function processingAt(currentStage) {
  const stages = ['f1', 'screening', 'f2', 'f3', 'f4']
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
    f3: idx > 3 ? f3 : null,
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
