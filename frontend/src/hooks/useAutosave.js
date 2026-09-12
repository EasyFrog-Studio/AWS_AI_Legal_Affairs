import { useEffect, useRef, useState } from 'react'

const DEFAULT_DEBOUNCE_MS = 1000

/**
 * 就地自動儲存狀態機:文字類欄位 debounce 後送出,`immediate` 欄位改變即送;
 * `flush()` 供 blur 與「AI 生成」前排空在途儲存;換 `resetKey`(案件)整份重置,
 * 同一案件時 `syncedFrom` 的新值逐欄合併——只在該欄本地值仍等於上次同步值時才採用,不蓋掉正在打的字。
 * 送出值與上次成功送出的值(以 `serialize` 比較)相同時跳過,避免多餘的呼叫。
 */
export function useAutosave({ initial, syncedFrom, resetKey, save, debounceMs = DEFAULT_DEBOUNCE_MS, serialize = JSON.stringify }) {
  const [value, setValue] = useState(initial)
  const [status, setStatus] = useState('idle') // idle | saving | saved | error
  const [error, setError] = useState('')

  const valueRef = useRef(value)
  const timerRef = useRef(null)
  const pendingRef = useRef(Promise.resolve())
  const syncedRef = useRef(syncedFrom)
  const lastSavedRef = useRef(serialize(initial))
  // 已排入但尚未確認成功的送出值:擋下「blur 觸發一次、緊接著呼叫端又 flush 一次」送出同一份值兩次
  const inFlightRef = useRef(null)
  const resetKeyRef = useRef(resetKey)

  useEffect(() => {
    if (syncedFrom == null) return
    if (resetKeyRef.current !== resetKey) {
      resetKeyRef.current = resetKey
      valueRef.current = syncedFrom
      syncedRef.current = syncedFrom
      lastSavedRef.current = serialize(syncedFrom)
      setValue(syncedFrom)
      setStatus('idle')
      setError('')
      return
    }
    const prevSynced = syncedRef.current
    syncedRef.current = syncedFrom
    setValue((current) => {
      let changed = false
      const merged = { ...current }
      for (const key of Object.keys(syncedFrom)) {
        if (current[key] === prevSynced[key] && current[key] !== syncedFrom[key]) {
          merged[key] = syncedFrom[key]
          changed = true
        }
      }
      if (changed) valueRef.current = merged
      return changed ? merged : current
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetKey, syncedFrom])

  function setLocal(next) {
    valueRef.current = next
    setValue(next)
    return next
  }

  async function doSave(next, serialized) {
    setStatus('saving')
    setError('')
    try {
      await save(next)
      lastSavedRef.current = serialized
      setStatus('saved')
    } catch (err) {
      // 錯誤要看得見,而且不清掉他剛打的字——重打一次是最沒必要的懲罰
      setStatus('error')
      setError(err.message || '儲存失敗，請重試。')
    } finally {
      if (inFlightRef.current === serialized) inFlightRef.current = null
    }
  }

  function trySave(next) {
    const serialized = serialize(next)
    if (serialized === lastSavedRef.current) return pendingRef.current
    if (serialized === inFlightRef.current) return pendingRef.current
    inFlightRef.current = serialized
    pendingRef.current = pendingRef.current.then(() => doSave(next, serialized))
    return pendingRef.current
  }

  function flush() {
    clearTimeout(timerRef.current)
    timerRef.current = null
    return trySave(valueRef.current)
  }

  function setField(key, fieldValue, { immediate = false } = {}) {
    const next = setLocal({ ...valueRef.current, [key]: fieldValue })
    clearTimeout(timerRef.current)
    if (immediate) {
      timerRef.current = null
      trySave(next)
    } else {
      timerRef.current = setTimeout(flush, debounceMs)
    }
    return next
  }

  const dirty = serialize(value) !== lastSavedRef.current

  return { value, setField, flush, status, error, dirty }
}
