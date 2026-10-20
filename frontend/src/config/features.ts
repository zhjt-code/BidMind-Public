type EnvMap = Record<string, string | boolean | undefined>

const env = (import.meta as ImportMeta & { env?: EnvMap }).env ?? {}

export function parseFeatureFlag(value: string | boolean | undefined): boolean {
  if (typeof value === 'boolean') return value
  if (value === undefined) return false

  const normalized = value.trim().toLowerCase()
  return normalized === 'true' || normalized === '1' || normalized === 'yes' || normalized === 'on'
}

export const isBillingEnabled = parseFeatureFlag(env.VITE_BILLING_ENABLED)
export const isPaymentEnabled = isBillingEnabled && parseFeatureFlag(env.VITE_PAYMENT_ENABLED)
export const isLlmCenterEnabled = parseFeatureFlag(env.VITE_LLM_CENTER_ENABLED)
// 邮箱验证硬拦截(需与后端 EMAIL_VERIFICATION_ENABLED 保持一致)
export const isEmailVerificationEnabled = parseFeatureFlag(env.VITE_EMAIL_VERIFICATION_ENABLED)
