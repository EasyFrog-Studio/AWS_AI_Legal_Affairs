import { useEffect, useState } from 'react'

// 分頁隱藏時回傳 true,場景據此暫停動畫
export default function usePaused() {
  const [paused, setPaused] = useState(() => document.hidden)
  useEffect(() => {
    const onChange = () => setPaused(document.hidden)
    document.addEventListener('visibilitychange', onChange)
    return () => document.removeEventListener('visibilitychange', onChange)
  }, [])
  return paused
}
