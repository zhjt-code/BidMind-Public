import { useCallback, useState } from 'react'

const getDisplayCount = (storageKey: string) => {
  try {
    const raw = localStorage.getItem(storageKey)
    const count = raw ? Number(raw) : 0
    return Number.isFinite(count) && count >= 0 ? count : 0
  } catch {
    return 0
  }
}

export function useLimitedDisplayDialog(storageKey: string, maxDisplays: number) {
  const [open, setOpen] = useState(false)

  const requestOpen = useCallback(() => {
    if (getDisplayCount(storageKey) >= maxDisplays) {
      return false
    }
    setOpen(true)
    return true
  }, [maxDisplays, storageKey])

  const dismiss = useCallback(() => {
    setOpen(false)
    try {
      const nextCount = Math.min(getDisplayCount(storageKey) + 1, maxDisplays)
      localStorage.setItem(storageKey, String(nextCount))
    } catch {
      // noop
    }
  }, [maxDisplays, storageKey])

  return { open, requestOpen, dismiss }
}
