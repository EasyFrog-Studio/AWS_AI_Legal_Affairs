import { useState } from 'react'
import Icon from './Icon.jsx'

// 副檔名是給拖曳來源沒帶 MIME 的情形留的退路,後端仍只收 PDF
function isPdf(file) {
  return file.type === 'application/pdf' || file.name.toLowerCase().endsWith('.pdf')
}

function fileSize(bytes) {
  const kb = bytes / 1024
  return kb < 1024 ? `${Math.max(1, Math.round(kb))} KB` : `${(kb / 1024).toFixed(1)} MB`
}

/** 落件框:拖曳與點選共用同一個 input,兩種來源走同一條檢查。
 * NewCase(收案)與 CaseDetail(待確認階段重傳)共用,兩處的落件手感必須一致。 */
export default function DropZone({ slot, fieldId, file, onFile, disabled }) {
  const [dragging, setDragging] = useState(false)
  const [rejected, setRejected] = useState('')

  function accept(picked) {
    if (!picked) return
    if (!isPdf(picked)) {
      setRejected(`只接受 PDF 檔：${picked.name} 無法帶入`)
      return
    }
    setRejected('')
    onFile(picked)
  }

  const state = [
    dragging ? 'dropzone--over' : '',
    file ? 'dropzone--filled' : '',
    rejected ? 'dropzone--error' : '',
    disabled ? 'dropzone--disabled' : '',
  ].join(' ')

  return (
    <div
      className={`dropzone ${state}`}
      onDragOver={(e) => {
        e.preventDefault()
        if (!disabled) setDragging(true)
      }}
      onDragLeave={(e) => {
        // 游標移到框內的 label 上也會觸發 dragleave,不濾掉的話拖曳標示會閃爍
        if (!e.currentTarget.contains(e.relatedTarget)) setDragging(false)
      }}
      onDrop={(e) => {
        e.preventDefault()
        setDragging(false)
        if (!disabled) accept(e.dataTransfer?.files?.[0])
      }}
    >
      <input
        id={fieldId}
        className="dropzone__input"
        type="file"
        accept="application/pdf"
        aria-label={`${slot.label} PDF`}
        onChange={(e) => accept(e.target.files?.[0])}
        disabled={disabled}
      />
      <label className="dropzone__face" htmlFor={fieldId}>
        <Icon name={file ? 'page-filled' : 'page-arrow'} className="dropzone__icon" />
        {file ? (
          <>
            <span className="dropzone__file">{file.name}</span>
            <span className="dropzone__size">{fileSize(file.size)}</span>
          </>
        ) : (
          <>
            <span className="dropzone__title">拖曳 PDF 到這裡</span>
            <span className="dropzone__hint">或點選此處選擇檔案</span>
          </>
        )}
      </label>
      {file && (
        <button
          type="button"
          className="btn-link dropzone__clear"
          onClick={() => {
            setRejected('')
            onFile(null)
          }}
          disabled={disabled}
        >
          移除
        </button>
      )}
      {rejected && (
        <p className="dropzone__reject" role="alert">
          {rejected}
        </p>
      )}
    </div>
  )
}
