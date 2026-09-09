// 三個文件槽的定義,NewCase(上傳)與 CaseDetail(確認/重傳)共用同一份,不重複寫兩次。
export const DOCUMENT_SLOTS = [
  { key: 'appeal', label: '訴願書', hint: '訴願人、原處分機關、請求事項與理由' },
  { key: 'service', label: '送達證書', hint: '送達時間與送達方式' },
  { key: 'disposition', label: '原處分書', hint: '罰鍰、法令依據與教示條款' },
]
