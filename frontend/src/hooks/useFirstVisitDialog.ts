import { useState, useCallback } from 'react'

function readFirstVisitOpen(storageKey: string): boolean {
  try {
    return localStorage.getItem(storageKey) !== 'true'
  } catch {
    return false
  }
}

export function useFirstVisitDialog(storageKey: string) {
  const [open, setOpen] = useState(() => readFirstVisitOpen(storageKey))
  const [prevStorageKey, setPrevStorageKey] = useState(storageKey)
  if (storageKey !== prevStorageKey) {
    setPrevStorageKey(storageKey)
    try {
      if (localStorage.getItem(storageKey) !== 'true') setOpen(true)
    } catch { /* noop */ }
  }

  const dismiss = useCallback(() => {
    setOpen(false)
    try {
      localStorage.setItem(storageKey, 'true')
    } catch { /* noop */ }
  }, [storageKey])

  return { open, dismiss }
}
