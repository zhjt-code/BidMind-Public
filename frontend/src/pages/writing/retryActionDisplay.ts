import { sanitizeUserFacingText } from '../../services/progressStatusTransforms.ts'
import {
  UPLOAD_FAILURE_FALLBACK,
  formatFailureMessage,
  resolveUploadFailureMessage,
} from '../../lib/formatFailureMessage.ts'

export {
  UPLOAD_FAILURE_FALLBACK,
  formatFailureMessage,
  resolveUploadFailureMessage,
}

const STAGE_LABELS: Record<string, string> = {
  // 通用调度阶段
  queued: '排队等待',
  starting: '任务启动',
  // 文档解析（writing_tender_process / document_process）
  processing: '文档处理',
  parsing: '文档解析',
  vl_recognition: '图片识别',
  chunking: '文本分块',
  vectorizing: '向量化',
  // 约束提取（writing_extract）
  extracting: '信息提取',
  extracting_hard_constraint: '硬约束提取',
  extracting_structure_constraint: '结构约束提取',
  extracting_generation_guide: '写作引导提取',
  dedup: '约束去重',
  saving: '结果保存',
  scoring_authority: '评分项提取',
  render_profile: '渲染配置识别',
  industry_profile: '行业画像',
  structure_analysis: '结构分析',
  directory_extract: '目录提取',
  // 目录生成（writing_outline）
  cleaning: '目录清理',
  generating: '目录生成',
  profiling: '行业画像',
  // 素材处理（writing_material_process）
  indexing: '素材索引',
  image_classify: '图片分类',
  label_resolve: '标签归一',
  tagging: '素材打标',
  // 素材匹配（writing_match）
  matching: '素材匹配',
  classifying: '素材分类',
  retrieving: '语义检索',
  materials: '素材处理',
  validating: '结果校验',
  // 内容生成与渲染
  composing: '内容生成',
  rendering: '文档渲染',
  // 投标审查
  reviewing: 'AI对比审查',
  // 串标审查
  global_features: '全局特征分析',
  comparing: '重点文件对比',
  clustering: '嫌疑群组识别',
  synthesizing: '综合研判',
  synthesis: '综合研判',
  analyzing: '串标分析',
  reporting: '报告生成',
}

/** 不承载任何定位信息的阶段码：展示它们只会挤占详情行 */
const UNINFORMATIVE_STAGES = new Set(['failed', 'unknown'])

const TECH_SCOPE_PATTERN =
  /\b(?:WritingRenderArtifact|VectorJobState|heartbeat|reset_scope|preserved_outputs|failed_stage|attempt)\b/i

export function formatRetryAttempt(count: number): string {
  return count > 0 ? `第 ${count} 次尝试` : ''
}

export function formatRetryStageLabel(stage: string | null | undefined): string {
  if (!stage) return ''
  if (UNINFORMATIVE_STAGES.has(stage)) return ''
  return STAGE_LABELS[stage] || '当前处理阶段'
}

export function formatRetryScopeItem(item: string): string {
  if (!item || TECH_SCOPE_PATTERN.test(item)) {
    return '相关中间结果'
  }
  return sanitizeUserFacingText(item) || '相关中间结果'
}
