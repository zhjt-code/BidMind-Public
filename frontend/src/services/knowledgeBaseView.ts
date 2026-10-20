import type { KnowledgeDocument } from './api'

export type KnowledgeLibraryCategory = 'enterprise' | 'standard' | 'experience'

export const KNOWLEDGE_AUTHORITY_LABELS: Record<string, string> = {
  national_standard: '国标',
  industry_standard: '行标',
  enterprise_standard: '企标',
  user_experience: '用户经验',
}

export const KNOWLEDGE_BUSINESS_TYPE_LABELS: Record<string, string> = {
  identity_document: '身份证明',
  business_license: '营业执照',
  bank_permit: '开户许可',
  qualification_cert: '资质证书',
  financial_document: '财务资料',
  tax_social_proof: '税务社保证明',
  performance_proof: '业绩案例',
  authorization: '授权材料',
  company_profile: '公司简介',
  personnel_info: '人员资料',
  equipment_info: '设备资料',
  standard: '规范标准',
  regulation: '法规政策',
  format_template: '格式模板',
  review_experience: '审查经验',
  normal_similarity: '串标白名单',
  risk_pattern: '风险模式',
  enterprise_other: '其他企业资料',
  standard_other: '其他规范资料',
  experience_other: '其他经验资料',
  other: '其他',
}

export const KNOWLEDGE_LIBRARY_CATEGORY_LABELS: Record<KnowledgeLibraryCategory, string> = {
  enterprise: '企业资料库',
  standard: '规范标准库',
  experience: '审查经验库',
}

export const KNOWLEDGE_SUPPORTED_FILE_EXTENSIONS = [
  '.pdf',
  '.docx',
  '.xlsx',
  '.txt',
  '.md',
  '.png',
  '.jpg',
  '.jpeg',
  '.webp',
  '.bmp',
  '.tif',
  '.tiff',
] as const
export const KNOWLEDGE_MAX_FILE_SIZE_MB = 200
export const KNOWLEDGE_MAX_FILE_SIZE_BYTES = KNOWLEDGE_MAX_FILE_SIZE_MB * 1024 * 1024

const ENTERPRISE_TYPES = new Set([
  'identity_document',
  'business_license',
  'bank_permit',
  'qualification_cert',
  'financial_document',
  'tax_social_proof',
  'performance_proof',
  'authorization',
  'company_profile',
  'personnel_info',
  'equipment_info',
  'enterprise_other',
])

const STANDARD_TYPES = new Set([
  'standard',
  'regulation',
  'format_template',
  'standard_other',
])

const EXPERIENCE_TYPES = new Set([
  'review_experience',
  'normal_similarity',
  'risk_pattern',
  'experience_other',
])

export const KNOWLEDGE_LIBRARY_TYPE_OPTIONS: Record<KnowledgeLibraryCategory, string[]> = {
  enterprise: [
    'company_profile',
    'qualification_cert',
    'performance_proof',
    'personnel_info',
    'equipment_info',
    'enterprise_other',
  ],
  standard: [
    'standard',
    'regulation',
    'format_template',
    'standard_other',
  ],
  experience: [
    'review_experience',
    'normal_similarity',
    'risk_pattern',
    'experience_other',
  ],
}

export function getDefaultBusinessTypeForCategory(category: KnowledgeLibraryCategory): string {
  return KNOWLEDGE_LIBRARY_TYPE_OPTIONS[category][0] || 'enterprise_other'
}

export function getDefaultFlowsForKnowledgeType(type: string): string[] {
  if (type === 'normal_similarity' || type === 'risk_pattern') {
    return ['collusion_check']
  }
  if (EXPERIENCE_TYPES.has(type)) {
    return ['bid_review']
  }
  if (STANDARD_TYPES.has(type)) {
    return ['bid_writing', 'bid_review', 'collusion_check']
  }
  return ['bid_writing']
}

function toDateKey(date: Date): string {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

export function isKnowledgeDocumentExpired(doc: Pick<KnowledgeDocument, 'validTo'>, now = new Date()): boolean {
  if (!doc.validTo) return false
  return doc.validTo < toDateKey(now)
}

export function canAutoUseKnowledgeDocument(
  doc: Pick<KnowledgeDocument, 'enabled' | 'reviewStatus' | 'processingStatus' | 'validTo'>,
  now = new Date(),
): boolean {
  return (
    doc.enabled
    && doc.reviewStatus === 'approved'
    && doc.processingStatus === 'completed'
    && !isKnowledgeDocumentExpired(doc, now)
  )
}

export function getKnowledgeBusinessTypeLabel(type: string): string {
  return KNOWLEDGE_BUSINESS_TYPE_LABELS[type] || type
}

export function getKnowledgeLibraryCategory(doc: Pick<KnowledgeDocument, 'businessType'>): KnowledgeLibraryCategory {
  if (STANDARD_TYPES.has(doc.businessType)) return 'standard'
  if (EXPERIENCE_TYPES.has(doc.businessType)) return 'experience'
  if (ENTERPRISE_TYPES.has(doc.businessType)) return 'enterprise'
  return 'experience'
}

export function getKnowledgeUnavailableReason(
  doc: Pick<KnowledgeDocument, 'enabled' | 'reviewStatus' | 'processingStatus' | 'validTo'>,
  now = new Date(),
): string | null {
  if (!doc.enabled) return '已停用'
  if (doc.reviewStatus !== 'approved') return '未审核通过'
  if (doc.processingStatus === 'failed') return '索引失败'
  if (doc.processingStatus !== 'completed') return '尚未完成索引'
  if (isKnowledgeDocumentExpired(doc, now)) return '已过期'
  return null
}

export function formatKnowledgeFileSize(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B'
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB']
  let size = bytes / 1024
  let unitIndex = 0
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024
    unitIndex += 1
  }
  return `${size >= 10 ? size.toFixed(1) : size.toFixed(2)} ${units[unitIndex]}`
}

export function getKnowledgeFileExtension(filename: string): string {
  const lastDot = filename.lastIndexOf('.')
  return lastDot >= 0 ? filename.slice(lastDot).toLowerCase() : ''
}

export function isSupportedKnowledgeFileName(filename: string): boolean {
  return KNOWLEDGE_SUPPORTED_FILE_EXTENSIONS.includes(
    getKnowledgeFileExtension(filename) as typeof KNOWLEDGE_SUPPORTED_FILE_EXTENSIONS[number],
  )
}

export function getKnowledgeFileKey(file: Pick<File, 'name' | 'size' | 'lastModified'>): string {
  return `${file.name}|${file.size}|${file.lastModified}`
}

export function validateKnowledgeFile(file: Pick<File, 'name' | 'size'>): string | null {
  if (!isSupportedKnowledgeFileName(file.name)) {
    return `不支持的文件格式: ${file.name}`
  }
  if (file.size > KNOWLEDGE_MAX_FILE_SIZE_BYTES) {
    return `文件超过${KNOWLEDGE_MAX_FILE_SIZE_MB}MB: ${file.name}`
  }
  return null
}
