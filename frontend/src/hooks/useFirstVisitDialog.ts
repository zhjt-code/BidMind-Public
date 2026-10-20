import { useState, useEffect, useCallback } from 'react'

export function useFirstVisitDialog(storageKey: string) {
  const [open, setOpen] = useState(false)

  useEffect(() => {
    try {
      if (localStorage.getItem(storageKey) !== 'true') {
        setOpen(true)
      }
    } catch { /* noop */ }
  }, [storageKey])

  const dismiss = useCallback(() => {
    setOpen(false)
    try {
      localStorage.setItem(storageKey, 'true')
    } catch { /* noop */ }
  }, [storageKey])

  return { open, dismiss }
}
