export interface MaterialDisplayLike {
  title?: string | null
  displayTitle?: string | null
  effectiveTitle?: string | null
}

export function getMaterialDisplayName(material: MaterialDisplayLike | null | undefined): string {
  const candidates = [
    material?.effectiveTitle,
    material?.displayTitle,
    material?.title,
  ]

  for (const candidate of candidates) {
    const normalized = (candidate || '').trim()
    if (normalized) return normalized
  }

  return ''
}
