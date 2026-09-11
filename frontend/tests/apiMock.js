import { vi } from 'vitest'

// 每個測試檔在頂層 vi.mock('../src/api', () => api) 後,用 api.* 設定回傳值
export const api = {
  getApiKey: vi.fn(() => 'k'),
  setApiKey: vi.fn(),
  clearApiKey: vi.fn(),
  listCases: vi.fn(async () => []),
  getCase: vi.fn(async () => null),
  getSource: vi.fn(async () => ({ text: '' })),
  getDecisionSkeleton: vi.fn(async () => ({
    blocks: [
      { kind: 'title', text: '新北市政府訴願決定書' },
      { kind: 'body', text: '　訴願人　王大明' },
      { kind: 'heading', text: '主　文' },
      { kind: 'slot', text: 'main_text' },
      { kind: 'heading', text: '事　實' },
      { kind: 'slot', text: 'fact' },
      { kind: 'heading', text: '理　由' },
      { kind: 'slot', text: 'reason' },
      { kind: 'body', text: '訴願審議委員會主任委員　　　　　　　' },
      { kind: 'body', text: '中華民國　　　　年　　　月　　　日' },
    ],
  })),
  updateDraft: vi.fn(async () => ({})),
  updateCaseInfo: vi.fn(async () => ({})),
  downloadDraftPdf: vi.fn(async () => {}),
  createCase: vi.fn(async () => ({ case_id: 'c-new' })),
  replaceDocument: vi.fn(async () => ({})),
  analyzeCase: vi.fn(async () => ({ ok: true })),
  reanalyzeCase: vi.fn(async () => ({ ok: true })),
  overrideScreening: vi.fn(async () => ({ ok: true, track: 'admissible' })),
  finalizeCase: vi.fn(async () => ({ finalized_at: '2026-08-27T05:00:00+00:00', pdf_location: '/tmp/c-1.pdf' })),
  health: vi.fn(async () => ({ status: 'ok', provider: 'mock', warning: '' })),
}
