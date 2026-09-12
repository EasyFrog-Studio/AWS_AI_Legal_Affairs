const API_KEY_STORAGE = 'apiKey'

function getApiKey() {
  return sessionStorage.getItem(API_KEY_STORAGE) || ''
}

function setApiKey(key) {
  sessionStorage.setItem(API_KEY_STORAGE, key)
}

function clearApiKey() {
  sessionStorage.removeItem(API_KEY_STORAGE)
}

async function request(path, options = {}) {
  const headers = { ...(options.headers || {}) }
  const key = getApiKey()
  if (key) {
    headers['X-API-Key'] = key
  }
  const res = await fetch(`/api${path}`, { ...options, headers })
  if (res.status === 401) {
    clearApiKey()
    window.location.href = '/login'
    throw new Error('未授權，請重新登入。')
  }
  if (!res.ok) {
    let message = `請求失敗（${res.status}）`
    let detail = null
    try {
      const body = await res.json()
      detail = body?.detail ?? null
      // detail 可能是物件(草稿版本衝突會帶最新內容),此時錯誤訊息另取,不把物件塞進字串
      if (typeof detail === 'string') message = detail
      else if (detail?.message) message = detail.message
    } catch {
      // ignore parse failure
    }
    const error = new Error(message)
    error.status = res.status
    error.detail = detail
    throw error
  }
  return res.json()
}

export function createCase(formData) {
  return request('/cases', { method: 'POST', body: formData })
}

export function replaceDocument(caseId, slot, formData) {
  return request(`/cases/${caseId}/documents/${slot}`, { method: 'PATCH', body: formData })
}

export function analyzeCase(caseId) {
  return request(`/cases/${caseId}/analyze`, { method: 'POST' })
}

export function listCases() {
  return request('/cases')
}

export function getCase(id) {
  return request(`/cases/${id}`)
}

export function getSource(key) {
  return request(`/source?key=${encodeURIComponent(key)}`)
}

export function updateCaseInfo(id, info) {
  return request(`/cases/${id}/f1`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(info),
  })
}

export function updateDraftText(id, body) {
  return request(`/cases/${id}/draft-text`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function overrideScreening(id, body) {
  return request(`/cases/${id}/screening`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function updateDraftResult(id, body) {
  return request(`/cases/${id}/draft/result`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export function reanalyzeCase(id) {
  return request(`/cases/${id}/reanalyze`, { method: 'POST' })
}

/** 參考見解的存檔 PDF:帶金鑰抓回 blob 再開新分頁。不能直接 window.open 端點——
 *  那條路不會帶 X-API-Key,而把金鑰塞進網址等於把它留在瀏覽記錄與 referer 裡。 */
export async function openSourceFile(key) {
  const res = await fetch(`/api/source/file?key=${encodeURIComponent(key)}`, {
    headers: { 'X-API-Key': getApiKey() },
  })
  if (res.status === 401) {
    clearApiKey()
    window.location.href = '/login'
    throw new Error('未授權，請重新登入。')
  }
  if (!res.ok) throw new Error(`找不到原文檔（${res.status}）`)
  return URL.createObjectURL(await res.blob())
}

async function downloadDraft(id, extension) {
  const res = await fetch(`/api/cases/${id}/draft.${extension}`, {
    headers: { 'X-API-Key': getApiKey() },
  })
  if (res.status === 401) {
    clearApiKey()
    window.location.href = '/login'
    throw new Error('未授權，請重新登入。')
  }
  if (!res.ok) throw new Error(`下載失敗（${res.status}）`)
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `決定書草稿_${id}.${extension}`
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

export function downloadDraftPdf(id) {
  return downloadDraft(id, 'pdf')
}

export function downloadDraftDocx(id) {
  return downloadDraft(id, 'docx')
}

export async function health() {
  const res = await fetch('/api/health')
  if (!res.ok) throw new Error('系統無回應')
  return res.json()
}

export { getApiKey, setApiKey, clearApiKey }
