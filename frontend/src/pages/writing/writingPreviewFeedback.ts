export function getReadableErrorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error) {
    const message = error.message.trim()
    if (message && message !== '{}') {
      return message
    }
  }
  return fallback
}

export function isStatusBlockedMessage(message: string | null | undefined): boolean {
  return Boolean(message && message.includes('当前状态') && message.includes('不允许'))
}

export function parseErrorDetailPayload(payloadText: string): string | null {
  if (!payloadText.trim()) {
    return null
  }

  try {
    const parsed = JSON.parse(payloadText) as { detail?: unknown; message?: unknown }
    if (typeof parsed.detail === 'string' && parsed.detail.trim()) {
      return parsed.detail
    }
    if (typeof parsed.message === 'string' && parsed.message.trim()) {
      return parsed.message
    }
  } catch {
    return null
  }

  return null
}
