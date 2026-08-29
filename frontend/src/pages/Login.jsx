import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { setApiKey } from '../api'
import { LoginScene, LoginBackdrop, playExit } from '../login-scene' // login-scene ①
import './Login.css'

export default function Login() {
  const [key, setKey] = useState('')
  const [status, setStatus] = useState('idle') // idle | loading | error | success
  const [errorMsg, setErrorMsg] = useState('')
  const navigate = useNavigate()
  const busy = status === 'loading' || status === 'success'

  async function handleSubmit(e) {
    e.preventDefault()
    if (!key.trim()) return
    setStatus('loading')
    setErrorMsg('')
    try {
      const res = await fetch('/api/cases', { headers: { 'X-API-Key': key } })
      if (!res.ok) {
        throw new Error('驗證失敗,請確認後重新輸入。')
      }
      setApiKey(key)
      setStatus('success')
      await playExit() // login-scene ④
      navigate('/')
    } catch (err) {
      setStatus('error')
      setErrorMsg(err.message || '驗證失敗,請確認後重新輸入。')
    }
  }

  return (
    <div className="login" data-state={status}>
      <LoginBackdrop />{/* login-scene ② */}
      <div className="login__card">
        <LoginScene />{/* login-scene ③ */}
        <div className="login__panel">
          <h1 className="login__title">訴願案件審理輔助系統</h1>
          <p className="login__tagline">新北市政府訴願案件審理 · 承辦人內部使用</p>
          <form onSubmit={handleSubmit}>
            <div className={`field ${status === 'error' ? 'field--error' : ''}`}>
              <input
                id="apiKey"
                className="input"
                type="password"
                value={key}
                onChange={(e) => setKey(e.target.value)}
                placeholder="請輸入密碼"
                disabled={busy}
              />
              {status === 'error' && <div className="field__error">{errorMsg}</div>}
            </div>
            <button
              type="submit"
              className={`btn btn-primary login__submit ${status === 'loading' ? 'btn--loading' : ''}`}
              disabled={busy || !key.trim()}
            >
              {status === 'loading' && <span className="btn__spinner" aria-hidden="true" />}
              {status === 'loading' ? '處理中…' : '進入系統'}
            </button>
          </form>
        </div>
      </div>
    </div>
  )
}
