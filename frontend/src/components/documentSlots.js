// 四個文件槽的定義,NewCase(上傳)與 CaseDetail(確認/重傳)共用同一份,不重複寫兩次。
// optional 的槽收案時可以留空:答辯書是原處分機關受理後才送來的,收案當下本來就不會有。
export const DOCUMENT_SLOTS = [
  { key: 'appeal', label: '訴願書', hint: '訴願人、原處分機關、請求事項與理由' },
  { key: 'service', label: '送達證書', hint: '送達時間與送達方式' },
  { key: 'disposition', label: '原處分書', hint: '罰鍰、法令依據與教示條款' },
  { key: 'answer', label: '訴願答辯書', hint: '原處分機關的答辯聲明、事實與理由', optional: true },
]

// F1 卷證總匯表的分組:每個欄位掛在它實際抄自的那一份文件底下。kind 決定呈現與編輯方式
// (mono = 等寬顯示的日期/文號,list = 多值欄位,編輯時一行一項)。
// 案由類別 / 爭點 / 援引法條不出自單一文件,故另立一組,不假裝它們有卷證出處。
export const F1_GROUPS = [
  {
    key: 'appeal',
    label: '訴願書',
    fields: [
      { label: '訴願人', key: 'appellant', kind: 'text' },
      { label: '收受或知悉日', key: 'receipt_date', kind: 'mono' },
      { label: '訴願理由', key: 'appeal_reasons', kind: 'list' },
    ],
  },
  {
    key: 'service',
    label: '送達證書',
    fields: [
      { label: '送達時間', key: 'service_date', kind: 'mono' },
      { label: '送達方式', key: 'service_method', kind: 'text' },
    ],
  },
  {
    key: 'disposition',
    label: '原處分書',
    fields: [
      { label: '原處分機關', key: 'agency', kind: 'text' },
      { label: '原處分日期', key: 'disposition_date', kind: 'mono' },
      { label: '原處分字號', key: 'disposition_no', kind: 'mono' },
      { label: '原處分內容', key: 'disposition_summary', kind: 'text' },
      { label: '罰鍰金額', key: 'disposition_fine', kind: 'text' },
      { label: '教示條款', key: 'disposition_notice_clause', kind: 'text' },
      { label: '處分相對人', key: 'disposition_recipient', kind: 'text' },
    ],
  },
  {
    key: 'answer',
    label: '訴願答辯書',
    // 機關受理後才送答辯書,收案當下本來就沒有;整組都空要講「尚未答辯」,不是留白
    emptyNote: '尚未答辯',
    fields: [
      { label: '答辯聲明', key: 'answer_statement', kind: 'text' },
      { label: '已自行撤銷或變更', key: 'answer_self_revoked', kind: 'text' },
      { label: '機關主張', key: 'answer_arguments', kind: 'list' },
    ],
  },
  {
    key: 'derived',
    label: '綜合判讀',
    fields: [
      { label: '案由類別', key: 'case_type', kind: 'text' },
      { label: '爭點', key: 'issues', kind: 'list' },
      { label: '援引法條', key: 'cited_articles', kind: 'list' },
    ],
  },
]
