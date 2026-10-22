/**
 * 职责: 文档质检前端领域类型(DTO 转换后的 camelCase 视图模型)
 * 不做: 不发请求、不做分组/排序逻辑(见 documentQualityTransforms)
 */

export type QaCategory =
  | 'structure' | 'toc' | 'layout' | 'language' | 'duplication'
  | 'numbering' | 'consistency' | 'reference' | 'hygiene' | 'integrity'

export type QaSeverity = 'P0' | 'P1' | 'P2' | 'P3'
export type QaCertainty = 'certain' | 'probable' | 'possible' | 'unknown'
export type QaDisposition = 'pending' | 'confirmed' | 'false_positive' | 'ignored'

/** 前端展示分组(后端十类收敛为五个用户心智组) */
export type QaGroupKey =
  | 'structure_nav'       // 结构与导航: structure/toc/numbering/reference
  | 'content_accuracy'    // 内容准确性: language/consistency
  | 'duplication_hygiene' // 重复与残留: duplication/hygiene
  | 'layout'              // 版式一致性: layout
  | 'page_objects'        // 页面与对象: integrity

export interface QaEvidenceItem {
  documentId: number | null
  bidPart: string | null
  page: number | null
  excerpt: string
  /** 归一化 bbox [x0,y0,x1,y1] (旋转后页面左上原点, 0..1); 文本级检查为 null */
  bbox: [number, number, number, number] | null
  role: 'primary' | 'comparison' | string
}

export interface QaEvidence {
  kind: 'single' | 'pair' | 'sequence' | string
  items: QaEvidenceItem[]
  expected?: string | null
  actual?: string | null
}

export interface QaFinding {
  id: number
  ruleCode: string
  category: QaCategory | string
  severity: QaSeverity | string
  certainty: QaCertainty | string
  title: string
  message: string
  suggestion: string | null
  evidence: QaEvidence
  documentId: number | null
  disposition: QaDisposition | string
  dispositionNote: string | null
  /** 处置乐观锁版本(PATCH 时作为 expected_version 回传) */
  version: number
  fingerprint: string
}

export interface QaScanDocument {
  documentId: number
  bidPart: string | null
  filename: string
  pageCount: number | null
  status: string
  /** 扫描件/图片页数(不在质检范围); 旧结果没有记录时为 null */
  imagePageCount: number | null
}

export interface QaScan {
  /** completed/degraded/failed/building/missing */
  status: string
  rulesetVersion: string | null
  generatedAt: string | null
  documents: QaScanDocument[]
  checkers: Array<{ code: string; status: string }>
  errorMessage: string | null
}

export interface QaSummary {
  total: number
  byCategory: Record<string, number>
  bySeverity: Record<string, number>
  byCertainty: Record<string, number>
  byDisposition: Record<string, number>
}

export interface DocumentQualityData {
  scan: QaScan
  summary: QaSummary
  findings: QaFinding[]
}

export interface QaFilterState {
  group: QaGroupKey | 'all'
  severity: QaSeverity | 'all'
  disposition: QaDisposition | 'all'
  certainty: QaCertainty | 'all'
}

export interface QaFilterCounts {
  byGroup: Record<string, number>
  bySeverity: Record<string, number>
  byDisposition: Record<string, number>
  byCertainty: Record<string, number>
}

export const DEFAULT_QA_FILTERS: QaFilterState = {
  group: 'all',
  severity: 'all',
  disposition: 'all',
  certainty: 'all',
}
