import { useLayoutEffect, useRef } from 'react'

/** 隨內容增高、不可手動縮放的貼上框:貼進來的全文長短差距極大,固定高度不是內捲就是留白。
 * hidden 為 true 時略過量測(祖先 display:none 時 scrollHeight 為 0),切回顯示時重算。 */
export default function AutoTextarea({ value, onChange, className = '', hidden = false, ...rest }) {
  const ref = useRef(null)
  useLayoutEffect(() => {
    const el = ref.current
    if (!el || hidden) return
    el.style.height = 'auto'
    el.style.height = `${el.scrollHeight}px`
  }, [value, hidden])
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
