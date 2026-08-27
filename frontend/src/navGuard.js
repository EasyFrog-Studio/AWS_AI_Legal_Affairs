import { createContext, useContext, useEffect } from 'react'

/**
 * 離開有未儲存修改的頁面前提示(brief 1.7.4「離開前必須提示」;3.3 行為 #6)。
 * - 頁面端:useNavGuard(isDirty, message) —— dirty 時登記守衛訊息並掛 beforeunload。
 * - shell 端:AppShell 以 useRef 建 guardRef、透過 NavGuardContext 提供;
 *   全域導覽連結 / 登出按鈕的 onClick 先呼叫 confirmLeave(guardRef, e),回傳 false 即不得導頁。
 * 擁有者:orchestrator。W1 只使用 NavGuardContext + confirmLeave;W5 只使用 useNavGuard。
 */
export const NavGuardContext = createContext({ guardRef: { current: null } })

export function useNavGuard(isDirty, message) {
  const { guardRef } = useContext(NavGuardContext)

  useEffect(() => {
    guardRef.current = isDirty ? message : null
    return () => {
      guardRef.current = null
    }
  }, [guardRef, isDirty, message])

  useEffect(() => {
    if (!isDirty) return undefined
    function handleBeforeUnload(e) {
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', handleBeforeUnload)
    return () => window.removeEventListener('beforeunload', handleBeforeUnload)
  }, [isDirty])
}

/** 有守衛且使用者取消 → 阻止事件並回傳 false;否則回傳 true。 */
export function confirmLeave(guardRef, e) {
  if (guardRef.current && !window.confirm(guardRef.current)) {
    e?.preventDefault()
    return false
  }
  return true
}
