/**
 * 职责: 公开页顶栏与页脚共用的区块导航锚点。
 * 不做: 不承载营销长文案，不伪造隐私政策或企业主体占位链接。
 */

export const publicNavItems = [
  { href: '#capabilities', label: '核心能力' },
  { href: '#platform', label: '统一平台' },
  { href: '#scenarios', label: '应用场景' },
  { href: '#knowledge', label: '知识库' },
  { href: '#trust', label: '数据与部署' },
] as const

export type PublicNavHref = (typeof publicNavItems)[number]['href']
