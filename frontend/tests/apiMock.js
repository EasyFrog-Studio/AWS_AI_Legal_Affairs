import { vi } from 'vitest'

// 每個測試檔在頂層 vi.mock('../src/api', () => api) 後,用 api.* 設定回傳值
export const api = {
  getApiKey: vi.fn(() => 'k'),
  setApiKey: vi.fn(),
  clearApiKey: vi.fn(),
  listCases: vi.fn(async () => []),
  getCase: vi.fn(async () => null),
  getSource: vi.fn(async () => ({ text: '' })),
  updateDraft: vi.fn(async () => ({})),
  downloadDraftPdf: vi.fn(async () => {}),
  createCase: vi.fn(async () => ({ case_id: 'c-new' })),
  health: vi.fn(async () => ({ status: 'ok' })),
}
