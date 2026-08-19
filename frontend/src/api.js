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
    throw new Error('未授權,請重新登入。')
  }
  if (!res.ok) {
    let message = `請求失敗(${res.status})`
    try {
      const body = await res.json()
      if (body?.detail) message = body.detail
    } catch {
      // ignore parse failure
    }
    throw new Error(message)
  }
  return res.json()
}

export function createCase(formData) {
  return request('/cases', { method: 'POST', body: formData })
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

export function updateDraft(id, body) {
  return request(`/cases/${id}/draft`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

export async function downloadDraftPdf(id) {
  const res = await fetch(`/api/cases/${id}/draft.pdf`, {
    headers: { 'X-API-Key': getApiKey() },
  })
  if (res.status === 401) {
    clearApiKey()
    window.location.href = '/login'
    throw new Error('未授權,請重新登入。')
  }
  if (!res.ok) throw new Error(`下載失敗(${res.status})`)
  const blob = await res.blob()
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = `決定書草稿_${id}.pdf`
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(url)
}

export async function health() {
  const res = await fetch('/api/health')
  if (!res.ok) throw new Error('系統無回應')
  return res.json()
}

export { getApiKey, setApiKey, clearApiKey }
