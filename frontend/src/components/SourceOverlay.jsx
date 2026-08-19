import { useEffect, useRef } from 'react'

export default function SourceOverlay({ content, onClose }) {
  const closeButtonRef = useRef(null)

  useEffect(() => {
    if (content?.url) {
      window.open(content.url, '_blank', 'noopener')
      onClose()
    }
  }, [content, onClose])

  const isOpen = Boolean(content) && !content.url

  useEffect(() => {
    if (!isOpen) return
    closeButtonRef.current?.focus()
    function handleKeyDown(e) {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [isOpen, onClose])

  if (!isOpen) return null

  return (
    <div className="overlay" onClick={onClose}>
      <div
        className="overlay__panel"
        role="dialog"
        aria-modal="true"
        aria-label="原文檢視"
        onClick={(e) => e.stopPropagation()}
      >
        <button
          type="button"
          ref={closeButtonRef}
          className="btn btn-secondary overlay__close"
          onClick={onClose}
        >
          關閉
        </button>
        <div className="overlay__text">{content.text}</div>
      </div>
    </div>
  )
}
