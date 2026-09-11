import { useLayoutEffect, useRef } from 'react'

/** 隨內容增高、不可手動縮放的貼上框:貼進來的全文長短差距極大,固定高度不是內捲就是留白。 */
export default function AutoTextarea({ value, onChange, className = '', ...rest }) {
  const ref = useRef(null)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight}px`
  }, [value])
  return (
    <textarea
      ref={ref}
      className={`textarea textarea--auto ${className}`}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      {...rest}
    />
  )
}
