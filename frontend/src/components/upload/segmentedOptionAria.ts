/**
 * 职责: 分段选项朗读名与计数附件文案
 * 不做: 不渲染 DOM、不绑定具体业务实体或路由
 */

export function segmentedCountAnnouncement(count: number, unit: string): string | undefined {
  if (!Number.isFinite(count) || count <= 0) return undefined
  return `${count} ${unit}`
}

export function segmentedOptionAriaLabel(label: string, announcedSuffix?: string): string {
  const suffix = announcedSuffix?.trim()
  return suffix ? `${label}，${suffix}` : label
}
