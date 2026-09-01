import { createContext, useContext, useEffect } from 'react'

/** 離開有未儲存修改的頁面前提示:頁面用 useNavGuard 登記,AppShell 的導覽 onClick 先過 confirmLeave。 */
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
