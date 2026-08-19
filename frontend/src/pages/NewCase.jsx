import { useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { createCase } from '../api'

export default function NewCase() {
  const [tab, setTab] = useState('pdf') // pdf | text
  const [file, setFile] = useState(null)
  const [text, setText] = useState('')
  const [status, setStatus] = useState('idle') // idle | loading | error
  const [errorMsg, setErrorMsg] = useState('')
  const fileInputRef = useRef(null)
  const navigate = useNavigate()

  const canSubmit = tab === 'pdf' ? Boolean(file) : text.trim().length > 0

  async function handleSubmit(e) {
    e.preventDefault()
    if (!canSubmit || status === 'loading') return
    setStatus('loading')
    setErrorMsg('')
    try {
      const formData = new FormData()
      if (tab === 'pdf') {
        formData.append('file', file)
      } else {
        formData.append('text', text)
      }
      const res = await createCase(formData)
      navigate(`/cases/${res.case_id}`)
    } catch (err) {
      setStatus('error')
      setErrorMsg(err.message || '送出失敗,請確認檔案格式後再試一次。')
    }
  }

  return (
    <div className="content">
      <div className="page-header">
        <h1 className="page-header__title">新建案件</h1>
      </div>

      <div className="tabs">
        <button
          type="button"
          className={`tab ${tab === 'pdf' ? 'tab--active' : ''}`}
          onClick={() => setTab('pdf')}
        >
          上傳 PDF
        </button>
        <button
          type="button"
          className={`tab ${tab === 'text' ? 'tab--active' : ''}`}
          onClick={() => setTab('text')}
        >
          貼上文字
        </button>
      </div>

      <form onSubmit={handleSubmit}>
        {tab === 'pdf' && (
          <div className="field">
            <label className="field__label" htmlFor="pdfFile">
              訴願書 PDF 檔案
            </label>
            <input
              id="pdfFile"
              ref={fileInputRef}
              className="input"
              type="file"
              accept="application/pdf"
              onChange={(e) => setFile(e.target.files?.[0] || null)}
              disabled={status === 'loading'}
            />
          </div>
        )}

        {tab === 'text' && (
          <div className="field">
            <label className="field__label" htmlFor="appealText">
              訴願書內容
            </label>
            <textarea
              id="appealText"
              className="textarea"
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder="請貼上訴願書全文"
              disabled={status === 'loading'}
            />
          </div>
        )}

        <button
          type="submit"
          className={`btn btn-primary ${status === 'loading' ? 'btn--loading' : ''}`}
          disabled={!canSubmit || status === 'loading'}
        >
          {status === 'loading' && <span className="btn__spinner" aria-hidden="true" />}
          {status === 'loading' ? '處理中…' : '送出訴願書'}
        </button>

        {status === 'error' && (
          <div className="form-result form-result--error">
            {errorMsg}
            <span className="form-result__hint">請確認檔案格式後再試一次,或改用文字貼上。</span>
          </div>
        )}
      </form>
    </div>
  )
}
