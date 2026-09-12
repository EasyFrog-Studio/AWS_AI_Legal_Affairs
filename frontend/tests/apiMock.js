import { vi } from 'vitest'

// 每個測試檔在頂層 vi.mock('../src/api', () => api) 後,用 api.* 設定回傳值
export const api = {
  getApiKey: vi.fn(() => 'k'),
  setApiKey: vi.fn(),
  clearApiKey: vi.fn(),
  listCases: vi.fn(async () => []),
  getCase: vi.fn(async () => null),
  getSource: vi.fn(async () => ({ text: '' })),
  openSourceFile: vi.fn(async () => 'blob:fake'),
  updateCaseInfo: vi.fn(async () => ({})),
  downloadDraftPdf: vi.fn(async () => {}),
  downloadDraftDocx: vi.fn(async () => {}),
  updateDraftText: vi.fn(async () => ({})),
  updateDraftResult: vi.fn(async () => ({ ok: true, draft_type: '駁回', overridden: true })),
  createCase: vi.fn(async () => ({ case_id: 'c-new' })),
  replaceDocument: vi.fn(async () => ({})),
  reanalyzeCase: vi.fn(async () => ({ ok: true })),
  updateDecisionHeader: vi.fn(async () => ({ ok: true })),
  getDocumentFile: vi.fn(async () => 'blob:fake-doc'),
  overrideScreening: vi.fn(async () => ({ ok: true, track: 'admissible' })),
  health: vi.fn(async () => ({ status: 'ok', provider: 'mock', warning: '' })),
}
