/**
 * 轻量设备指纹(软信号)
 *
 * 取浏览器稳定特征做 SHA-256(crypto.subtle), 不引第三方库, 用于"设备指纹软上限"
 * 这类弱风控信号(非主拦截, 主拦截为邮箱验证)。失败/不可用时返回空串,
 * 后端按"无指纹"放行。如需更强稳定性, 可在此替换为 FingerprintJS。
 */

function canvasFingerprint(): string {
  try {
    const canvas = document.createElement('canvas')
    const ctx = canvas.getContext('2d')
    if (!ctx) return ''
    ctx.textBaseline = 'top'
    ctx.font = "14px 'Arial'"
    ctx.fillStyle = '#f60'
    ctx.fillRect(125, 1, 62, 20)
    ctx.fillStyle = '#069'
    ctx.fillText('BidMind-fp', 2, 15)
    return canvas.toDataURL().slice(-64)
  } catch {
    return ''
  }
}

async function sha256Hex(input: string): Promise<string> {
  try {
    if (typeof crypto !== 'undefined' && crypto.subtle) {
      const data = new TextEncoder().encode(input)
      const buf = await crypto.subtle.digest('SHA-256', data)
      return Array.from(new Uint8Array(buf))
        .map((b) => b.toString(16).padStart(2, '0'))
        .join('')
    }
  } catch {
    // fall through to weak hash
  }
  let h = 5381
  for (let i = 0; i < input.length; i++) {
    h = ((h << 5) + h + input.charCodeAt(i)) >>> 0
  }
  return h.toString(16)
}

export async function getDeviceFingerprint(): Promise<string> {
  try {
    const nav = navigator as Navigator & { deviceMemory?: number; languages?: string[] }
    const parts = [
      nav.userAgent || '',
      nav.language || '',
      (nav.languages || []).join(','),
      `${screen.width}x${screen.height}x${screen.colorDepth}`,
      String(new Date().getTimezoneOffset()),
      Intl.DateTimeFormat().resolvedOptions().timeZone || '',
      String(nav.hardwareConcurrency || ''),
      String(nav.deviceMemory || ''),
      canvasFingerprint(),
    ]
    return await sha256Hex(parts.join('|'))
  } catch {
    return ''
  }
}
