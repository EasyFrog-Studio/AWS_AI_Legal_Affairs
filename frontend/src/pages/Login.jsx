import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { setApiKey } from '../api'

export default function Login() {
  const [key, setKey] = useState('')
  const [status, setStatus] = useState('idle') // idle | loading | error
  const [errorMsg, setErrorMsg] = useState('')
  const navigate = useNavigate()

  async function handleSubmit(e) {
    e.preventDefault()
    if (!key.trim()) return
    setStatus('loading')
    setErrorMsg('')
    try {
      const res = await fetch('/api/cases', { headers: { 'X-API-Key': key } })
      if (!res.ok) {
        throw new Error('API Key 驗證失敗,請確認後重新輸入。')
      }
      setApiKey(key)
      navigate('/')
    } catch (err) {
      setStatus('error')
      setErrorMsg(err.message || 'API Key 驗證失敗,請確認後重新輸入。')
    }
  }

  return (
    <div className="login-shell">
      <div className="login-card">
        <h1 className="login-card__title">訴願案件審理輔助系統</h1>
        <form onSubmit={handleSubmit}>
          <div className={`field ${status === 'error' ? 'field--error' : ''}`}>
            <label className="field__label" htmlFor="apiKey">
              API Key
            </label>
            <input
              id="apiKey"
              className="input"
              type="password"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder="請輸入 API Key"
              disabled={status === 'loading'}
            />
            {status === 'error' && <div className="field__error">{errorMsg}</div>}
          </div>
          <button
            type="submit"
            className={`btn btn-primary ${status === 'loading' ? 'btn--loading' : ''}`}
            disabled={status === 'loading' || !key.trim()}
          >
            {status === 'loading' && <span className="btn__spinner" aria-hidden="true" />}
            {status === 'loading' ? '處理中…' : '進入系統'}
          </button>
        </form>
      </div>
    </div>
  )
}
