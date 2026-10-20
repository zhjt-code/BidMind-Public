export function getWritingSkipVlStorageKey(projectId: number): string {
  return `writing-upload-skip-vl:${projectId}`
}

export function readPersistedWritingSkipVl(projectId: number): boolean | null {
  if (typeof window === 'undefined' || !Number.isFinite(projectId)) return null
  const raw = window.sessionStorage.getItem(getWritingSkipVlStorageKey(projectId))
  if (raw == null) return null
  return raw === '1'
}

export function persistWritingSkipVl(projectId: number, value: boolean): void {
  if (typeof window === 'undefined' || !Number.isFinite(projectId)) return
  window.sessionStorage.setItem(getWritingSkipVlStorageKey(projectId), value ? '1' : '0')
}

export function clearPersistedWritingSkipVl(projectId: number): void {
  if (typeof window === 'undefined' || !Number.isFinite(projectId)) return
  window.sessionStorage.removeItem(getWritingSkipVlStorageKey(projectId))
}
