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
  createCase: vi.fn(async () => ({ case_id: 'c-new' })),
  replaceDocument: vi.fn(async () => ({})),
  analyzeCase: vi.fn(async () => ({ ok: true })),
  reanalyzeCase: vi.fn(async () => ({ ok: true })),
  overrideScreening: vi.fn(async () => ({ ok: true, track: 'admissible' })),
  finalizeCase: vi.fn(async () => ({ finalized_at: '2026-08-27T05:00:00+00:00', pdf_location: '/tmp/c-1.pdf' })),
  health: vi.fn(async () => ({ status: 'ok', provider: 'mock', warning: '' })),
}
