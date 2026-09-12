// 四個文件槽的定義,NewCase(上傳)與 CaseDetail(確認/重傳)共用同一份,不重複寫兩次。
// optional 的槽收案時可以留空:送達證書非必備文書(觀念通知等案件本無此文書,缺件時期間一律
// 視為未逾期)。
export const DOCUMENT_SLOTS = [
  { key: 'appeal', label: '訴願書' },
  { key: 'service', label: '送達證書', optional: true },
  { key: 'disposition', label: '原處分書' },
  { key: 'answer', label: '訴願答辯書' },
]

// 送達方式值域,同 backend app/models.py SERVICE_METHODS(訴願法準用行政程序法§72-74)
const SERVICE_METHOD_OPTIONS = [
  { value: '', label: '—' },
  { value: '本人', label: '本人' },
  { value: '同居人', label: '同居人' },
  { value: '受雇人', label: '受雇人' },
  { value: '接收郵件人員', label: '接收郵件人員' },
  { value: '留置', label: '留置' },
  { value: '寄存', label: '寄存' },
  { value: '未載明', label: '未載明' },
]

// 代理人或送達代收人身分值域,同 backend app/models.py AGENT_ROLES;空字串代表該區塊未填
const AGENT_ROLE_OPTIONS = [
  { value: '', label: '未填' },
  { value: '代理人', label: '代理人' },
  { value: '送達代收人', label: '送達代收人' },
]

// F1 頁的欄位全表:五個文件分頁,每頁再依表單實際印刷分節。key/標籤/型別逐項對照
// backend app/models.py CaseInfo,權威在 example6 的實際印刷標籤(見規格書 §1.5)。
// kind: text(單行)/ long(多行 AutoTextarea)/ list(一行一項)/ date(RocDatePicker)/ select(下拉)。
export const F1_GROUPS = [
  {
    key: 'appeal',
    label: '訴願書',
    sections: [
      {
        label: '訴願人',
        fields: [
          { label: '訴願人姓名', key: 'appellant', kind: 'text' },
          { label: '身分證明文件字號', key: 'appellant_id_no', kind: 'text' },
          { label: '出生年月日', key: 'appellant_birth_date', kind: 'date' },
          { label: '住址', key: 'appellant_address', kind: 'text' },
          { label: '聯絡電話', key: 'appellant_phone', kind: 'text' },
        ],
      },
      {
        label: '代表人',
        fields: [
          { label: '代表人姓名', key: 'representative_name', kind: 'text' },
          { label: '代表人身分證明文件字號', key: 'representative_id_no', kind: 'text' },
          { label: '代表人出生年月日', key: 'representative_birth_date', kind: 'date' },
        ],
      },
      {
        label: '代理人或送達代收人',
        fields: [
          { label: '代理人或送達代收人(身分)', key: 'agent_role', kind: 'select', options: AGENT_ROLE_OPTIONS },
          { label: '代理人姓名', key: 'agent_name', kind: 'text' },
          { label: '代理人身分證明文件字號', key: 'agent_id_no', kind: 'text' },
          { label: '代理人出生年月日', key: 'agent_birth_date', kind: 'date' },
          { label: '代理人住址', key: 'agent_address', kind: 'text' },
          { label: '代理人聯絡電話', key: 'agent_phone', kind: 'text' },
        ],
      },
      {
        label: '其他',
        fields: [
          { label: '受理訴願機關', key: 'receiving_agency', kind: 'text' },
          { label: '訴願請求事項', key: 'appeal_request', kind: 'long' },
          { label: '事實', key: 'appeal_facts', kind: 'list' },
          { label: '理由', key: 'appeal_reasons', kind: 'list' },
          { label: '附送證件', key: 'appeal_attachments', kind: 'list' },
          { label: '收受或知悉行政處分日期', key: 'receipt_date', kind: 'date' },
          { label: '訴願書日期', key: 'appeal_date', kind: 'date' },
          { label: '機關收文日期', key: 'appeal_filed_date', kind: 'date' },
        ],
      },
    ],
  },
  {
    key: 'service',
    label: '送達證書',
    sections: [
      {
        label: '送達證書',
        fields: [
          { label: '受送達人', key: 'service_recipient', kind: 'text' },
          { label: '受送達人地址', key: 'service_address', kind: 'text' },
          { label: '文號', key: 'service_doc_no', kind: 'text' },
          { label: '送達文書', key: 'service_doc_title', kind: 'text' },
          { label: '送達處所', key: 'service_place', kind: 'text' },
          { label: '送達時間', key: 'service_date', kind: 'date' },
          { label: '送達方式', key: 'service_method', kind: 'select', options: SERVICE_METHOD_OPTIONS },
          { label: '收領人', key: 'service_receiver', kind: 'text' },
          { label: '寄存機關', key: 'service_deposit_office', kind: 'text' },
        ],
      },
    ],
  },
  {
    key: 'disposition',
    label: '原處分書',
    sections: [
      {
        label: '處分文書',
        fields: [
          { label: '處分機關', key: 'agency', kind: 'text' },
          { label: '發文日期', key: 'disposition_date', kind: 'date' },
          { label: '發文字號', key: 'disposition_no', kind: 'text' },
        ],
      },
      {
        label: '受處分人',
        fields: [
          { label: '受處分人姓名或名稱', key: 'disposition_recipient', kind: 'text' },
          { label: '受處分人性別', key: 'disposition_recipient_gender', kind: 'text' },
          { label: '受處分人出生日期', key: 'disposition_recipient_birth_date', kind: 'date' },
          { label: '統一編號或護照號碼', key: 'disposition_recipient_id_no', kind: 'text' },
          { label: '其他足資辨別之特徵', key: 'disposition_recipient_features', kind: 'text' },
          { label: '受處分人地址', key: 'disposition_recipient_address', kind: 'text' },
        ],
      },
      {
        label: '場所',
        fields: [{ label: '場所名稱及地址', key: 'disposition_premises', kind: 'text' }],
      },
      {
        label: '代表人或管理人',
        fields: [
          { label: '代表人或管理人姓名', key: 'disposition_manager_name', kind: 'text' },
          { label: '代表人或管理人性別', key: 'disposition_manager_gender', kind: 'text' },
          { label: '代表人或管理人出生日期', key: 'disposition_manager_birth_date', kind: 'date' },
        ],
      },
      {
        label: '處分內容',
        fields: [
          { label: '主旨', key: 'disposition_summary', kind: 'long' },
          { label: '事實', key: 'disposition_facts', kind: 'long' },
          { label: '理由及法令依據', key: 'disposition_grounds', kind: 'long' },
          { label: '罰鍰金額', key: 'disposition_fine', kind: 'text' },
          { label: '教示條款', key: 'disposition_notice_clause', kind: 'long' },
        ],
      },
      {
        label: '繳款',
        fields: [
          { label: '繳款期限', key: 'disposition_payment_deadline', kind: 'date' },
          { label: '繳款地點', key: 'disposition_payment_place', kind: 'text' },
        ],
      },
    ],
  },
  {
    key: 'answer',
    label: '訴願答辯書',
    sections: [
      {
        label: '答辯文書',
        fields: [
          { label: '答辯書所載訴願人', key: 'answer_appellant', kind: 'text' },
          { label: '答辯書所載訴願人地址', key: 'answer_appellant_address', kind: 'text' },
          { label: '原處分機關', key: 'answer_agency', kind: 'text' },
          { label: '發文日期', key: 'answer_date', kind: 'date' },
          { label: '發文字號', key: 'answer_doc_no', kind: 'text' },
        ],
      },
      {
        label: '答辯內容',
        fields: [
          { label: '答辯聲明', key: 'answer_statement', kind: 'long' },
          { label: '答辯事實', key: 'answer_facts', kind: 'long' },
          { label: '理由', key: 'answer_arguments', kind: 'list' },
          { label: '已自行撤銷或變更', key: 'answer_self_revoked', kind: 'text' },
          { label: '證物', key: 'answer_evidence', kind: 'list' },
        ],
      },
      {
        label: '署名',
        fields: [
          { label: '代表人', key: 'answer_representative', kind: 'text' },
          { label: '送達代收人', key: 'answer_service_agent', kind: 'text' },
        ],
      },
    ],
  },
  {
    key: 'derived',
    label: '綜合判讀',
    sections: [
      {
        label: '綜合判讀',
        fields: [
          { label: '案由類別', key: 'case_type', kind: 'text' },
          { label: '爭點', key: 'issues', kind: 'list' },
          { label: '援引法條', key: 'cited_articles', kind: 'list' },
        ],
      },
    ],
  },
]
